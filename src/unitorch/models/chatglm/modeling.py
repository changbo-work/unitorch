# Copyright (c) FULIUCANSHENG.
# Licensed under the MIT License.

import os
import logging
import torch
import torch.nn as nn
import torch.nn.functional as F
import transformers
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from unitorch.models.chatglm.modeling_chatglm import (
    ChatGLMModel,
    ChatGLMConfig,
    ChatGLMForConditionalGeneration,
)
from unitorch.utils.decorators import replace
from unitorch.models import GenericModel, GenericOutputs


class ChatGLMForClassification(GenericModel):
    """
    ChatGLM model for classification tasks.

    Args:
        config_path (str): Path to the configuration file.
        num_classes (Optional[int]): Number of classes for classification. Defaults to 1.
        hidden_dropout_prob (Optional[float]): The dropout probability for the hidden layers. Defaults to 0.1.
        gradient_checkpointing (Optional[bool]): Whether to use gradient checkpointing. Defaults to False.
    """
    def __init__(
        self,
        config_path: str,
        num_classes: Optional[int] = 1,
        hidden_dropout_prob: Optional[float] = 0.1,
        gradient_checkpointing: Optional[bool] = False,
    ):
        super().__init__()
        self.config = ChatGLMConfig.from_json_file(config_path)
        self.config.gradient_checkpointing = gradient_checkpointing
        self.transformer = ChatGLMModel(self.config)
        self.dropout = nn.Dropout(hidden_dropout_prob)
        self.classifier = nn.Linear(self.config.hidden_size, num_classes)
        self.init_weights()

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Forward pass of the model.

        Args:
            input_ids (torch.Tensor): Input IDs of shape (batch_size, sequence_length).
            attention_mask (Optional[torch.Tensor]): Attention mask of shape (batch_size, sequence_length).
            position_ids (Optional[torch.Tensor]): Position IDs of shape (batch_size, sequence_length).

        Returns:
            (torch.Tensor):Logits of shape (batch_size, num_classes).
        """
        outputs = self.transformer(
            input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
        )[0].permute(1, 0, 2)
        pooled_output = outputs[:, -1]
        pooled_output = self.dropout(pooled_output)
        logits = self.classifier(pooled_output)
        return logits


class ChatGLMForPretrain(GenericModel):
    def __init__(
        self,
        config_path: str,
        gradient_checkpointing: Optional[bool] = False,
    ):
        """
        Initializes the ChatGLMForPretrain model.

        Args:
            config_path (str): Path to the configuration file.
            gradient_checkpointing (Optional[bool]): Whether to use gradient checkpointing. Defaults to False.
        """
        super().__init__()
        self.config = ChatGLMConfig.from_json_file(config_path)
        self.config.gradient_checkpointing = gradient_checkpointing
        self.transformer = ChatGLMModel(self.config)
        self.lm_head = nn.Linear(
            self.config.hidden_size, self.config.vocab_size, bias=False
        )
        self.init_weights()

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
        input_ids_label: Optional[torch.Tensor] = None,
        attention_mask_label: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Performs forward pass of the ChatGLMForPretrain model.

        Args:
            input_ids (Optional[torch.Tensor]): Tensor of input token IDs. Defaults to None.
            attention_mask (Optional[torch.Tensor]): Tensor of attention mask. Defaults to None.
            position_ids (Optional[torch.Tensor]): Tensor of position IDs. Defaults to None.
            input_ids_label (Optional[torch.Tensor]): Tensor of token IDs for labeling. Defaults to None.
            attention_mask_label (Optional[torch.Tensor]): Tensor of token mask for labeling. Defaults to None.

        Returns:
            (torch.Tensor):Tensor of loss.
        """
        outputs = self.transformer(
            input_ids=input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
        )
        predict_logits = self.lm_head(outputs[0].permute(1, 0, 2))
        batch_size, seq_len, num_classes = predict_logits.size()
        logits = predict_logits.contiguous().view(batch_size * seq_len, num_classes)
        targets = input_ids_label.contiguous().view(-1).long()
        masks = attention_mask_label.contiguous().view(-1)
        loss = nn.CrossEntropyLoss(reduction="none")(logits, targets)
        loss = loss * masks.float()
        loss = loss.contiguous().view(batch_size, seq_len).sum(1) / torch.max(
            masks.contiguous().view(batch_size, seq_len).float().sum(1),
            torch.ones(batch_size).to(masks.device),
        )
        loss = torch.mean(loss)
        return loss


class ChatGLMForGeneration(GenericModel):
    prefix_keys_in_state_dict = {"^(?!model\.).*": "model."}

    def __init__(
        self,
        config_path: str,
        gradient_checkpointing: Optional[bool] = False,
    ):
        """
        Initializes the ChatGLMForGeneration model.

        Args:
            config_path (str): Path to the configuration file.
            gradient_checkpointing (Optional[bool]): Whether to use gradient checkpointing. Defaults to False.
        """
        super().__init__()
        self.config = ChatGLMConfig.from_json_file(config_path)
        self.config.gradient_checkpointing = gradient_checkpointing
        self.model = ChatGLMForConditionalGeneration(self.config)
        self.init_weights()

    def forward(
        self,
        input_ids: Optional[torch.Tensor],
        attention_mask: Optional[torch.Tensor],
        position_ids: Optional[torch.Tensor],
    ) -> torch.Tensor:
        """
        Performs forward pass of the ChatGLMForGeneration model.

        Args:
            input_ids (Optional[torch.Tensor]): Tensor of input token IDs.
            attention_mask (Optional[torch.Tensor]): Tensor of attention mask.
            position_ids (Optional[torch.Tensor]): Tensor of position IDs.

        Returns:
            (torch.Tensor):Tensor of logits.
        """
        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
            return_dict=True,
        )
        logits = outputs.logits
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
    ) -> GenericOutputs:
        """
        Generates sequences using the ChatGLMForGeneration model.

        Args:
            input_ids: The input token IDs.
            num_beams (Optional[int]): The number of beams for beam search. Defaults to 5.
            decoder_start_token_id (Optional[int]): The ID of the decoder start token. Defaults to 2.
            decoder_end_token_id (Optional[int]): The ID of the decoder end token. Defaults to 2.
            num_return_sequences (Optional[int]): The number of generated sequences to return. Defaults to 1.
            min_gen_seq_length (Optional[int]): The minimum length of the generated sequences. Defaults to 0.
            max_gen_seq_length (Optional[int]): The maximum length of the generated sequences. Defaults to 48.
            repetition_penalty (Optional[float]): The repetition penalty for beam search. Defaults to 1.0.
            no_repeat_ngram_size (Optional[int]): The size of n-grams to avoid repetition in beam search. Defaults to 0.
            early_stopping (Optional[bool]): Whether to stop generation early based on the specified conditions. Defaults to True.
            length_penalty (Optional[float]): The length penalty for beam search. Defaults to 1.0.
            num_beam_groups (Optional[int]): The number of beam groups for diverse beam search. Defaults to 1.
            diversity_penalty (Optional[float]): The diversity penalty for diverse beam search. Defaults to 0.0.
            do_sample (Optional[bool]): Whether to use sampling for generation. Defaults to False.
            temperature (Optional[float]): The temperature for sampling. Defaults to 1.0.
            top_k (Optional[int]): The value of k for top-k sampling. Defaults to 50.
            top_p (Optional[float]): The value of p for top-p sampling. Defaults to 1.0.

        Returns:
            GenericOutputs: The generated sequences and their scores.
        """
        input_seq_length = input_ids.size(1)
        outputs = self.model.generate(
            input_ids,
            max_length=max_gen_seq_length + input_seq_length,
            min_length=min_gen_seq_length + input_seq_length,
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
        outputs.sequences[:, :, : sequences.size(-1) - input_seq_length].copy_(
            sequences[:, :, input_seq_length : sequences.size(-1)]
        )

        if num_return_sequences == 1:
            outputs.sequences = outputs.sequences.reshape(-1, max_gen_seq_length)

        return GenericOutputs(
            sequences=outputs.sequences.long(),
            sequences_scores=outputs.sequences_scores,
        )