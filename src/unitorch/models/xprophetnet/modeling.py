# Copyright (c) FULIUCANSHENG.
# Licensed under the MIT License.

import os
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from torch import device, Tensor
import json
import torch
import torch.nn as nn
import torch.nn.functional as F
import transformers
from transformers import (
    XLMProphetNetConfig,
    XLMProphetNetModel,
    XLMProphetNetForConditionalGeneration,
)

from unitorch.models import GenericModel, GenericOutputs
from unitorch.utils.decorators import replace


@replace(
    transformers.models.xlm_prophetnet.modeling_xlm_prophetnet.XLMProphetNetAttention
)
class ProphetNetAttentionV2(
    transformers.models.xlm_prophetnet.modeling_xlm_prophetnet.XLMProphetNetAttention
):
    def __init__(
        self,
        config: XLMProphetNetConfig,
        num_attn_heads: int,
    ):
        super().__init__(config=config, num_attn_heads=num_attn_heads)

    def _shape(self, tensor: torch.Tensor, seq_len: int, bsz: int):
        return (
            tensor.view(bsz, seq_len, self.num_attn_heads, self.head_dim)
            .transpose(1, 2)
            .contiguous()
        )

    def forward(
        self,
        hidden_states,
        key_value_states: Optional[torch.Tensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        layer_head_mask: Optional[torch.Tensor] = None,
        past_key_value: Optional[Tuple[torch.Tensor]] = None,
        output_attentions: Optional[bool] = False,
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        batch_size, tgt_len, hidden_size = hidden_states.size()
        kv_batch_size = batch_size

        # if key_value_states are provided this layer is used as a cross-attention layer
        # for the decoder
        is_cross_attention = key_value_states is not None
        assert list(hidden_states.size()) == [
            batch_size,
            tgt_len,
            hidden_size,
        ], f"Size of hidden states should be {batch_size, tgt_len, hidden_size}, but is {hidden_states.size()}"

        # previous time steps are cached - no need to recompute key and value if they are static
        query_states = self.query_proj(hidden_states) / (self.head_dim**0.5)

        if is_cross_attention and past_key_value is not None:
            # reuse k,v, cross_attentions
            key_states = past_key_value[0]
            value_states = past_key_value[1]
            kv_batch_size = key_states.size(0)
        elif is_cross_attention:
            # cross_attentions
            kv_batch_size = key_value_states.size(0)
            key_states = self._shape(self.key_proj(key_value_states), -1, kv_batch_size)
            value_states = self._shape(
                self.value_proj(key_value_states), -1, kv_batch_size
            )
        else:
            # self_attention
            key_states = self._shape(self.key_proj(hidden_states), -1, batch_size)
            value_states = self._shape(self.value_proj(hidden_states), -1, batch_size)

        if is_cross_attention:
            # if cross_attention save Tuple(torch.Tensor, torch.Tensor) of all cross attention key/value_states.
            # Further calls to cross_attention layer can then reuse all cross-attention
            # key/value_states (first "if" case)
            # if encoder bi-directional self-attention `past_key_value` is always `None`
            past_key_value = (key_states, value_states)

        # project states into the correct shape
        proj_shape = (batch_size, self.num_attn_heads, -1, self.head_dim)
        query_states = self._shape(query_states, tgt_len, batch_size).view(*proj_shape)
        kv_proj_shape = (kv_batch_size, self.num_attn_heads, -1, self.head_dim)
        key_states = key_states.view(*kv_proj_shape)
        value_states = value_states.view(*kv_proj_shape)

        src_len = key_states.size(2)
        if is_cross_attention and kv_batch_size != batch_size:
            attn_weights = torch.einsum(
                "bxhtd,bhsd->bxhts",
                query_states.view(kv_batch_size, -1, *query_states.size()[1:]),
                key_states.view(kv_batch_size, *key_states.size()[1:]),
            )
            attn_weights = attn_weights.reshape(-1, *attn_weights.size()[-3:])
        else:
            attn_weights = torch.einsum(
                "bsij,bsjk->bsik", query_states, key_states.transpose(2, 3)
            )

        expected_shape = (batch_size, self.num_attn_heads, tgt_len, src_len)
        if attn_weights.size() != expected_shape:
            raise ValueError(
                f"Attention weights should have size {expected_shape}, but is {attn_weights.size()}"
            )

        # This is part of a workaround to get around fork/join parallelism not supporting Optional types.

        if attention_mask is not None and attention_mask.dim() == 0:
            attention_mask = None
        expected_shape = (batch_size, self.num_attn_heads, 1, src_len)
        if attention_mask is not None and attention_mask.size() != expected_shape:
            raise ValueError(
                f"Attention mask should have size {expected_shape}, but is {attention_mask.size()}"
            )
        if attention_mask is not None:  # don't attend to padding symbols
            attn_weights = attn_weights + attention_mask
        if output_attentions:
            attn_weights_reshaped = attn_weights
        else:
            attn_weights_reshaped = None

        attn_weights = F.softmax(attn_weights, dim=-1)
        if layer_head_mask is not None:
            assert layer_head_mask.size() == (
                self.num_attn_heads,
            ), f"Head mask for a single layer should be of size {(self.num_attn_heads,)}, but is {layer_head_mask.size()}"
            attn_weights = layer_head_mask.view(1, -1, 1, 1) * attn_weights.view(
                batch_size, self.num_attn_heads, tgt_len, src_len
            )
            attn_weights = attn_weights.view(
                batch_size * self.num_attn_heads, tgt_len, src_len
            )

            # apply head_mask also on attn_weights_reshaped which is used for n-gram attention inside the model
            attn_weights_reshaped = (
                layer_head_mask.view(1, -1, 1, 1) * attn_weights_reshaped
            )

        attn_probs = F.dropout(
            attn_weights,
            p=self.attention_dropout,
            training=self.training,
        )

        if is_cross_attention and kv_batch_size != batch_size:
            attn_probs = attn_probs.to(value_states.dtype)
            attn_output = torch.einsum(
                "bxhts,bhsd->bxhtd",
                attn_probs.view(kv_batch_size, -1, *attn_probs.size()[1:]),
                value_states.view(kv_batch_size, *value_states.size()[1:]),
            )
            attn_output = attn_output.reshape(-1, *attn_output.size()[-3:])
        else:
            attn_output = torch.einsum("bsij,bsjk->bsik", attn_probs, value_states)

        expected_shape = (batch_size, self.num_attn_heads, tgt_len, self.head_dim)
        if attn_output.size() != expected_shape:
            raise ValueError(
                f"`attn_output` should have shape {expected_shape}, but is of shape {attn_output.size()}"
            )

        attn_output = attn_output.transpose(1, 2).reshape(
            batch_size, tgt_len, hidden_size
        )
        attn_output = self.out_proj(attn_output)

        attn_output = nn.functional.dropout(
            attn_output, p=self.dropout, training=self.training
        )
        return attn_output, attn_weights_reshaped, past_key_value


class XProphetNetForGeneration(GenericModel):
    prefix_keys_in_state_dict = {"^(?!model\.).*": "model."}

    def __init__(
        self,
        config_path: str,
        freeze_input_embedding: Optional[bool] = True,
        gradient_checkpointing: Optional[bool] = False,
    ):
        super().__init__()

        self.config = XLMProphetNetConfig.from_json_file(config_path)
        self.config.gradient_checkpointing = gradient_checkpointing
        self.model = XLMProphetNetForConditionalGeneration(self.config)

        if freeze_input_embedding:
            for param in self.model.get_input_embeddings().parameters():
                param.requires_grad = False

        self.init_weights()

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        decoder_input_ids: torch.Tensor,
        decoder_attention_mask: torch.Tensor,
    ):
        """
        Performs forward pass of the XPegasusForGeneration model.

        Args:
            input_ids (torch.Tensor): Tensor of input token IDs.
            attention_mask (torch.Tensor): Tensor of attention mask.
            decoder_input_ids (torch.Tensor): Tensor of decoder input token IDs.
            decoder_attention_mask (torch.Tensor): Tensor of decoder attention mask.

        Returns:
            (torch.Tensor):The model's logits.
        """
        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            decoder_input_ids=decoder_input_ids,
            decoder_attention_mask=decoder_attention_mask,
            return_dict=True,
        )
        logits = outputs.logits.unsqueeze(1)
        if outputs.logits_ngram is not None:
            logits_ngram = outputs.logits_ngram
            logits = torch.cat([logits, logits_ngram], dim=1)
        return logits

    @torch.no_grad()
    def generate(
        self,
        input_ids: torch.Tensor,
        num_beams: Optional[int] = 5,
        decoder_start_token_id: Optional[int] = 2,
        decoder_end_token_id: Optional[int] = 2,
        num_return_sequences: Optional[int] = 1,
        min_gen_seq_length: Optional[int] = 0,
        max_gen_seq_length: Optional[int] = 48,
        repetition_penalty: Optional[float] = 1.0,
        no_repeat_ngram_size: Optional[int] = 0,
        early_stopping: Optional[bool] = True,
        length_penalty: Optional[float] = 1.0,
        num_beam_groups: Optional[int] = 1,
        diversity_penalty: Optional[float] = 0.0,
        do_sample: Optional[bool] = False,
        temperature: Optional[float] = 1.0,
        top_k: Optional[int] = 50,
        top_p: Optional[float] = 1.0,
    ):
        """
        Generates sequences using the XPegasusForGeneration model.

        Args:
            input_ids: The input token IDs.
            num_beams (int, optional): The number of beams for beam search. Defaults to 5.
            decoder_start_token_id (int, optional): The decoder's start token ID. Defaults to 2.
            decoder_end_token_id (int, optional): The decoder's end token ID. Defaults to 2.
            num_return_sequences (int, optional): The number of generated sequences to return. Defaults to 1.
            min_gen_seq_length (int, optional): The minimum length of the generated sequences. Defaults to 0.
            max_gen_seq_length (int, optional): The maximum length of the generated sequences. Defaults to 48.
            repetition_penalty (float, optional): The repetition penalty. Defaults to 1.0.
            no_repeat_ngram_size (int, optional): The size of n-grams to avoid repeating. Defaults to 0.
            early_stopping (bool, optional): Whether to stop generation early. Defaults to True.
            length_penalty (float, optional): The length penalty. Defaults to 1.0.
            num_beam_groups (int, optional): The number of beam groups for diverse beam search. Defaults to 1.
            diversity_penalty (float, optional): The diversity penalty. Defaults to 0.0.
            do_sample (bool, optional): Whether to use sampling for generation. Defaults to False.
            temperature (float, optional): The temperature for sampling. Defaults to 1.0.
            top_k (int, optional): The value for top-k sampling. Defaults to 50.
            top_p (float, optional): The value for top-p (nucleus) sampling. Defaults to 1.0.

        Returns:
            GenericOutputs: The generated sequences and their scores.
        """
        outputs = self.model.generate(
            input_ids,
            max_length=max_gen_seq_length,
            min_length=min_gen_seq_length,
            num_beams=num_beams,
            do_sample=do_sample,
            decoder_start_token_id=decoder_start_token_id,
            no_repeat_ngram_size=no_repeat_ngram_size,
            early_stopping=early_stopping,
            length_penalty=length_penalty,
            repetition_penalty=repetition_penalty,
            num_return_sequences=num_return_sequences,
            bos_token_id=decoder_start_token_id,
            eos_token_id=decoder_end_token_id,
            num_beam_groups=num_beam_groups,
            diversity_penalty=diversity_penalty,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            return_dict_in_generate=True,
            output_scores=True,
        )

        sequences = outputs.sequences.reshape(
            -1, num_return_sequences, outputs.sequences.size(-1)
        )
        outputs.sequences = torch.zeros(
            sequences.size(0), num_return_sequences, max_gen_seq_length
        ).to(device=sequences.device)
        outputs.sequences[:, :, : sequences.size(-1)].copy_(sequences)

        if num_return_sequences == 1:
            outputs.sequences = outputs.sequences.reshape(-1, max_gen_seq_length)

        return GenericOutputs(
            sequences=outputs.sequences,
            sequences_scores=outputs.sequences_scores,
        )