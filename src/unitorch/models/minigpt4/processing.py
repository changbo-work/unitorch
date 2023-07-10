# Copyright (c) FULIUCANSHENG.
# Licensed under the MIT License.

import os
import random
from functools import partial
from PIL import Image
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import numpy as np
import torch

from transformers import LlamaTokenizer
from transformers import ViTImageProcessor

from unitorch.utils import pop_value, truncate_sequence_pair
from unitorch.models import (
    HfTextClassificationProcessor,
    HfTextGenerationProcessor,
    HfImageClassificationProcessor,
    GenericOutputs,
)


class MiniGPT4ViTLlamaProcessor(
    HfTextClassificationProcessor,
    HfImageClassificationProcessor,
    HfTextGenerationProcessor,
):
    def __init__(
        self,
        vocab_file: str,
        vision_config_path: str,
        max_seq_length: Optional[int] = 128,
        max_gen_seq_length: Optional[int] = 48,
    ):
        """
        Initialize the LlamaProcessor.

        Args:
            vocab_file (str): Path to the vocabulary file.
            max_seq_length (int, optional): Maximum sequence length for text classification. Defaults to 128.
            max_gen_seq_length (int, optional): Maximum sequence length for text generation. Defaults to 48.
        """
        tokenizer = LlamaTokenizer(vocab_file=vocab_file)
        tokenizer.cls_token = tokenizer.bos_token
        tokenizer.sep_token = tokenizer.eos_token
        tokenizer.pad_token = tokenizer.unk_token
        tokenizer.cls_token_id = tokenizer.bos_token_id
        tokenizer.sep_token_id = tokenizer.eos_token_id
        tokenizer.pad_token_id = tokenizer.unk_token_id
        vision_processor = ViTImageProcessor.from_json_file(vision_config_path)
        HfTextClassificationProcessor.__init__(
            self,
            tokenizer=tokenizer,
            max_seq_length=max_seq_length,
        )
        HfTextGenerationProcessor.__init__(
            self,
            tokenizer=tokenizer,
            max_seq_length=max_seq_length,
            max_gen_seq_length=max_gen_seq_length,
        )
        HfImageClassificationProcessor.__init__(self, vision_processor=vision_processor)

    def prompt(
        self,
        prefix_text: str,
        suffix_text: str,
        image: Image.Image,
        max_seq_length: Optional[int] = None,
    ):
        """
        Process text as a prompt.

        Args:
            text (str): Input text.
            max_seq_length (int, optional): Maximum sequence length. Defaults to None.

        Returns:
            GenericOutputs: Processed input_ids tensor.
        """
        max_seq_length = pop_value(
            max_seq_length,
            self.max_seq_length,
        )
        prefix_tokens = self.tokenizer.tokenize(str(prefix_text))
        suffix_tokens = self.tokenizer.tokenize(str(suffix_text))[: max_seq_length - 1]

        prefix_tokens = prefix_tokens[: max_seq_length - len(suffix_tokens) - 1]
        prefix_tokens = [self.bos_token] + prefix_tokens
        padding = [self.pad_token] * (max_seq_length - len(prefix_tokens))
        prefix_tokens = padding + prefix_tokens
        prefix_input_ids = self.tokenizer.convert_tokens_to_ids(prefix_tokens)
        suffix_input_ids = self.tokenizer.convert_tokens_to_ids(suffix_tokens)

        outputs = HfImageClassificationProcessor.classification(
            self,
            image=image,
        )

        return GenericOutputs(
            prefix_input_ids=torch.tensor(prefix_input_ids, dtype=torch.long),
            suffix_input_ids=torch.tensor(suffix_input_ids, dtype=torch.long),
            pixel_values=outputs.pixel_values,
        )

    def generation_inputs(
        self,
        prefix_text: str,
        suffix_text: str,
        image: Image.Image,
        max_seq_length: Optional[int] = None,
    ):
        """
        Process text for generation inputs.

        Args:
            text (str): Input text.
            max_seq_length (int, optional): Maximum sequence length. Defaults to None.

        Returns:
            GenericOutputs: Processed input_ids tensor.
        """
        max_seq_length = pop_value(
            max_seq_length,
            self.max_seq_length,
        )
        prefix_tokens = self.tokenizer.tokenize(str(prefix_text))
        suffix_tokens = self.tokenizer.tokenize(str(suffix_text))

        prefix_tokens = prefix_tokens[: max_seq_length - 1]
        prefix_tokens = [self.bos_token] + prefix_tokens
        padding = [self.pad_token] * (max_seq_length - len(prefix_tokens))
        prefix_tokens = padding + prefix_tokens
        prefix_input_ids = self.tokenizer.convert_tokens_to_ids(prefix_tokens)
        suffix_input_ids = self.tokenizer.convert_tokens_to_ids(suffix_tokens)

        outputs = HfImageClassificationProcessor.classification(
            self,
            image=image,
        )

        return GenericOutputs(
            prefix_input_ids=torch.tensor(prefix_input_ids, dtype=torch.long),
            suffix_input_ids=torch.tensor(suffix_input_ids, dtype=torch.long),
            pixel_values=outputs.pixel_values,
        )

    def generation_labels(
        self,
        text: str,
        max_gen_seq_length: Optional[int] = None,
    ):
        """
        Process text for generation labels.

        Args:
            text (str): Input text.
            max_gen_seq_length (int, optional): Maximum generation sequence length. Defaults to None.

        Returns:
            GenericOutputs: Processed input_ids and attention_mask tensors.
        """
        max_gen_seq_length = pop_value(
            max_gen_seq_length,
            self.max_gen_seq_length,
        )
        tokens = self.tokenizer.tokenize(str(text))[: max_gen_seq_length - 1] + [
            self.eos_token
        ]
        padding = [self.pad_token] * (max_gen_seq_length - len(tokens))
        input_ids = self.tokenizer.convert_tokens_to_ids(tokens)
        attention_mask = [1] * len(input_ids)

        padding = [0] * (max_gen_seq_length - len(input_ids))
        input_ids += [self.pad_token_id] * len(padding)
        attention_mask += padding

        assert len(input_ids) == max_gen_seq_length
        assert len(attention_mask) == max_gen_seq_length
        return GenericOutputs(
            input_ids=torch.tensor(input_ids, dtype=torch.long),
            attention_mask=torch.tensor(attention_mask, dtype=torch.long),
        )

    def generation(
        self,
        prefix_text: str,
        suffix_text: str,
        text_pair: str,
        image: Image.Image,
        max_seq_length: Optional[int] = None,
        max_gen_seq_length: Optional[int] = None,
    ):
        """
        Process text for generation.

        Args:
            text (str): Input text.
            text_pair (str): Input text pair.
            max_seq_length (int, optional): Maximum sequence length. Defaults to None.
            max_gen_seq_length (int, optional): Maximum generation sequence length. Defaults to None.

        Returns:
            GenericOutputs: Processed input_ids, attention_mask, input_ids_label, and attention_mask_label tensors.
        """
        max_seq_length = pop_value(
            max_seq_length,
            self.max_seq_length,
        )
        max_gen_seq_length = pop_value(
            max_gen_seq_length,
            self.max_gen_seq_length,
        )
        max_seq_length = max_seq_length + max_gen_seq_length

        prefix_tokens = self.tokenizer.tokenize(str(prefix_text))
        suffix_tokens = self.tokenizer.tokenize(str(suffix_text))

        prefix_tokens = prefix_tokens[: max_seq_length - 1]
        prefix_tokens = [self.bos_token] + prefix_tokens

        prefix_input_ids = self.tokenizer.convert_tokens_to_ids(prefix_tokens)
        suffix_input_ids = self.tokenizer.convert_tokens_to_ids(suffix_tokens)

        padding_a = [self.pad_token] * (max_seq_length - len(prefix_tokens))
        prefix_input_ids = self.tokenizer.convert_tokens_to_ids(
            padding_a + prefix_tokens
        )
        prefix_attention_mask = [0] * len(padding_a) + [1] * len(prefix_tokens)

        suffix_input_ids = self.tokenizer.convert_tokens_to_ids(suffix_tokens)
        suffix_attention_mask = [1] * len(suffix_tokens)

        tokens_pair = self.tokenizer.tokenize(str(text_pair))[
            : max_gen_seq_length - 1
        ] + [self.eos_token]

        padding_b = [self.pad_token] * (max_gen_seq_length - len(tokens_pair))
        input_ids_pair = self.tokenizer.convert_tokens_to_ids(tokens_pair + padding_b)
        attention_mask_pair = [1] * len(tokens_pair) + [0] * len(padding_b)

        suffix_seq_length = len(suffix_tokens)
        tokens_label = tokens_pair + [self.pad_token] * (
            max_gen_seq_length - len(tokens_pair) + 1
        )
        input_ids_label = self.tokenizer.convert_tokens_to_ids(tokens_label)
        input_ids_label = [0] * (suffix_seq_length - 1) + input_ids_label
        attention_mask_label = [1] * len(tokens_pair) + [0] * (
            max_gen_seq_length - len(tokens_pair) + 1
        )
        attention_mask_label = [0] * (suffix_seq_length - 1) + attention_mask_label

        outputs = HfImageClassificationProcessor.classification(
            self,
            image=image,
        )

        return GenericOutputs(
            prefix_input_ids=torch.tensor(prefix_input_ids, dtype=torch.long),
            prefix_attention_mask=torch.tensor(prefix_attention_mask, dtype=torch.long),
            suffix_input_ids=torch.tensor(suffix_input_ids, dtype=torch.long),
            suffix_attention_mask=torch.tensor(suffix_attention_mask, dtype=torch.long),
            input_ids_pair=torch.tensor(input_ids_pair, dtype=torch.long),
            attention_mask_pair=torch.tensor(attention_mask_pair, dtype=torch.long),
            input_ids_label=torch.tensor(input_ids_label, dtype=torch.long),
            attention_mask_label=torch.tensor(attention_mask_label, dtype=torch.long),
            pixel_values=outputs.pixel_values,
        )
