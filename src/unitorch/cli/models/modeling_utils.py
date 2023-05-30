# Copyright (c) FULIUCANSHENG.
# Licensed under the MIT License.

import abc
import pandas as pd
import torch
import torch.nn as nn
import torch.distributed as dist
import torch.nn.functional as F
from dataclasses import dataclass, field, fields
from itertools import chain
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union


@dataclass
class TensorsMixin:
    def __post_init__(
        self,
    ):
        if not hasattr(self, "__tensors__"):
            self.__tensors__ = {}
        for key, tensor in self.__tensors__.items():
            assert isinstance(tensor, torch.Tensor)
            setattr(self, key, tensor)

    @classmethod
    def union(cls, *list_tensors, dim=0):
        if len(list_tensors) == 0:
            return cls()
        first_tensors = list_tensors[0]
        keys = list(first_tensors.__tensors__.keys())
        for tensors in list_tensors:
            assert keys == list(tensors.__tensors__.keys())

        new_tensors = {}
        for key in keys:
            new_tensor = [tensors.__tensors__[key] for tensors in list_tensors]
            new_tensors[key] = torch.cat(new_tensor, dim=dim)
        return cls(**new_tensors)

    @classmethod
    def stack(cls, *list_tensors, dim=0):
        if len(list_tensors) == 0:
            return cls()
        first_tensors = list_tensors[0]
        keys = list(first_tensors.__tensors__.keys())
        for tensors in list_tensors:
            assert keys == list(tensors.__tensors__.keys())

        new_tensors = {}
        for key in keys:
            new_tensor = [tensors.__tensors__[key] for tensors in list_tensors]
            new_tensors[key] = torch.stack(new_tensor, dim=dim)

        return cls(**new_tensors)

    def add(self, tensors: Union[Dict, "TensorsMixin"]):
        if isinstance(tensors, dict):
            tensors_dict = tensors
        else:
            tensors_dict = tensors.__tensors__
        for key, value in tensors_dict.items():
            assert isinstance(value, torch.Tensor)
            if key in self.__tensors__:
                assert ValueError(f"{key} already in the tensor.")
            else:
                self.__tensors__[key] = value
                setattr(self, key, value)

    def cpu(self, inplace=False):
        new_tensors = {}
        for key, value in self.__tensors__.items():
            new_value = value.cpu()
            new_tensors[key] = new_value
        if inplace:
            for key, value in new_tensors.items():
                self.__tensors__[key] = value
            return
        return type(self)(**new_tensors)

    def cuda(self, inplace=False):
        new_tensors = {}
        for key, value in self.__tensors__.items():
            new_value = value.cuda()
            new_tensors[key] = new_value
        if inplace:
            for key, value in new_tensors.items():
                self.__tensors__[key] = value
            return
        return type(self)(**new_tensors)

    def sync(self, dim=0, inplace=False):
        new_tensors = {}
        for key, value in self.__tensors__.items():
            new_value = [value.clone() for _ in range(dist.get_world_size())]
            dist.all_gather(new_value, value)
            new_value = torch.cat(new_value, dim=dim)
            new_tensors[key] = new_value
        if inplace:
            for key, value in new_tensors.items():
                self.__tensors__[key] = value
            return
        return type(self)(**new_tensors)

    def dict(self):
        return self.__tensors__


@dataclass
class ListTensorsMixin:
    def __post_init__(
        self,
    ):
        if not hasattr(self, "__list_tensors__"):
            self.__list_tensors__ = {}
        new_list_tensors = {}
        for key, value in self.__list_tensors__.items():
            if isinstance(value, torch.Tensor):
                value = [value]
            new_list_tensors[key] = value
        self.__list_tensors__ = new_list_tensors
        for key, list_tensor in self.__list_tensors__.items():
            assert isinstance(list_tensor, list)
            assert all([isinstance(tensor, torch.Tensor) for tensor in list_tensor])
            setattr(self, key, list_tensor)

    @classmethod
    def union(cls, *list_of_list_tensors, dim=0):
        if len(list_of_list_tensors) == 0:
            return cls()
        first_list_tensors = list_of_list_tensors[0]
        keys = list(first_list_tensors.__list_tensors__.keys())
        for list_tensors in list_of_list_tensors:
            assert keys == list(list_tensors.__list_tensors__.keys())

        new_list_tensors = {}
        for key in keys:
            new_list_tensor = [
                list_tensors.__list_tensors__[key]
                for list_tensors in list_of_list_tensors
            ]
            new_list_tensors[key] = list(chain.from_iterable(new_list_tensor))

        return cls(**new_list_tensors)

    @classmethod
    def stack(cls, *list_of_list_tensors, dim=0):
        if len(list_of_list_tensors) == 0:
            return cls()
        first_list_tensors = list_of_list_tensors[0]
        keys = list(first_list_tensors.__list_tensors__.keys())
        for list_tensors in list_of_list_tensors:
            assert keys == list(list_tensors.__list_tensors__.keys())

        new_list_tensors = {}
        for key in keys:
            new_list_tensor = [
                list_tensors.__list_tensors__[key]
                for list_tensors in list_of_list_tensors
            ]
            assert all([len(list_tensor) == 1 for list_tensor in new_list_tensor])
            new_list_tensors[key] = list(chain.from_iterable(new_list_tensor))

        return cls(**new_list_tensors)

    def add(self, list_tensors: Union[Dict, "ListTensorsMixin"]):
        if isinstance(list_tensors, dict):
            list_tensors_dict = list_tensors
        else:
            list_tensors_dict = list_tensors.__list_tensors__
        for key, value in list_tensors_dict.items():
            assert isinstance(value, list)
            if key in self.__list_tensors__:
                assert ValueError(f"{key} already in the list tensor.")
            else:
                if isinstance(value, torch.Tensor):
                    value = [value]
                self.__list_tensors__[key] = value
                setattr(self, key, value)

    def cpu(self, inplace=False):
        new_list_tensors = {}
        for key, value in self.__list_tensors__.items():
            new_value = [_value.cpu() for _value in value]
            new_list_tensors[key] = new_value
        if inplace:
            for key, value in new_list_tensors.items():
                self.__tensors__[key] = value
            return
        return type(self)(**new_list_tensors)

    def cuda(self, inplace=False):
        new_list_tensors = {}
        for key, value in self.__list_tensors__.items():
            new_value = [_value.cuda() for _value in value]
            new_list_tensors[key] = new_value
        if inplace:
            for key, value in new_list_tensors.items():
                self.__list_tensors__[key] = value
            return
        return type(self)(**new_list_tensors)

    def sync(self, inplace=False):
        new_list_tensors = {}
        for key, value in self.__list_tensors__.items():
            new_value = [[] for _ in range(dist.get_world_size())]
            dist.all_gather_object(new_value, value)
            new_value = list(chain.from_iterable(new_value))
            new_list_tensors[key] = new_value
        if inplace:
            for key, value in new_list_tensors.items():
                self.__list_tensors__[key] = value
            return
        return type(self)(**new_list_tensors)

    def dict(self):
        return self.__list_tensors__


class CombineTensorsMixin(TensorsMixin, ListTensorsMixin):
    def __init__(
        self,
        dict_of_tensors: Dict[str, torch.Tensor] = dict(),
        dict_of_list_tensors: Dict[str, List[torch.Tensor]] = dict(),
    ):
        self.__tensors__ = dict_of_tensors
        self.__list_tensors__ = dict_of_list_tensors
        self.__post_init__()

    def __post_init__(
        self,
    ):
        TensorsMixin.__post_init__(self)
        ListTensorsMixin.__post_init__(self)

    @classmethod
    def union(cls, *list_of_mix_tensors, dim=0):
        if len(list_of_mix_tensors) == 0:
            return cls()
        first_mix_tensors = list_of_mix_tensors[0]
        tensors_keys = list(first_mix_tensors.__tensors__.keys())
        list_tensors_keys = list(first_mix_tensors.__list_tensors__.keys())
        for mix_tensors in list_of_mix_tensors:
            assert tensors_keys == list(mix_tensors.__tensors__.keys())
            assert list_tensors_keys == list(mix_tensors.__list_tensors__.keys())

        new_tensors = {}
        for key in tensors_keys:
            new_tensor = [
                mix_tensors.__tensors__[key] for mix_tensors in list_of_mix_tensors
            ]
            new_tensors[key] = torch.cat(new_tensor, dim=dim)

        new_list_tensors = {}
        for key in list_tensors_keys:
            new_list_tensor = [
                mix_tensors.__list_tensors__[key] for mix_tensors in list_of_mix_tensors
            ]
            new_list_tensors[key] = list(chain.from_iterable(new_list_tensor))

        return cls(dict_of_tensors=new_tensors, dict_of_list_tensors=new_list_tensors)

    @classmethod
    def stack(cls, *list_of_mix_tensors, dim=0):
        if len(list_of_mix_tensors) == 0:
            return cls()
        first_mix_tensors = list_of_mix_tensors[0]
        tensors_keys = list(first_mix_tensors.__tensors__.keys())
        list_tensors_keys = list(first_mix_tensors.__list_tensors__.keys())
        for mix_tensors in list_of_mix_tensors:
            assert tensors_keys == list(mix_tensors.__tensors__.keys())
            assert list_tensors_keys == list(mix_tensors.__list_tensors__.keys())

        new_tensors = {}
        for key in tensors_keys:
            new_tensor = [
                mix_tensors.__tensors__[key] for mix_tensors in list_of_mix_tensors
            ]
            new_tensors[key] = torch.stack(new_tensor, dim=dim)

        new_list_tensors = {}
        for key in list_tensors_keys:
            new_list_tensor = [
                mix_tensors.__list_tensors__[key] for mix_tensors in list_of_mix_tensors
            ]
            new_list_tensors[key] = new_list_tensor

        return cls(dict_of_tensors=new_tensors, dict_of_list_tensors=new_list_tensors)

    def add(self, mix_tensors: Union[TensorsMixin, ListTensorsMixin]):
        if isinstance(mix_tensors, TensorsMixin):
            TensorsMixin.add(self, mix_tensors.__tensors__)
        if isinstance(mix_tensors, ListTensorsMixin):
            ListTensorsMixin.add(self, mix_tensors.__list_tensors__)

    def cpu(self, inplace=False):
        new_tensors = TensorsMixin.cpu(self, inplace=inplace)
        new_list_tensors = ListTensorsMixin.cpu(self, inplace=inplace)
        if inplace:
            return
        return type(self)(
            dict_of_tensors=new_tensors.__tensors__,
            dict_of_list_tensors=new_list_tensors.__list_tensors__,
        )

    def cuda(self, inplace=False):
        new_tensors = TensorsMixin.cuda(self, inplace=inplace)
        new_list_tensors = ListTensorsMixin.cuda(self, inplace=inplace)
        if inplace:
            return
        return type(self)(
            dict_of_tensors=new_tensors.__tensors__,
            dict_of_list_tensors=new_list_tensors.__list_tensors__,
        )

    def sync(self, inplace=False):
        new_tensors = TensorsMixin.sync(self, inplace=inplace)
        new_list_tensors = ListTensorsMixin.sync(self, inplace=inplace)
        if inplace:
            return
        return type(self)(
            dict_of_tensors=new_tensors.__tensors__,
            dict_of_list_tensors=new_list_tensors.__list_tensors__,
        )

    def dict(self):
        return {**self.__tensors__, **self.__list_tensors__}


class ModelInputs(metaclass=abc.ABCMeta):
    pass


class ModelOutputs(metaclass=abc.ABCMeta):
    pass


class ModelTargets(metaclass=abc.ABCMeta):
    pass


@dataclass(init=False)
class TensorsInputs(ModelInputs, TensorsMixin):
    def __init__(self, inputs: Optional[Dict] = dict(), **kwargs):
        self.__tensors__ = {}
        for k, v in {**inputs, **kwargs}.items():
            if not hasattr(self, k):
                setattr(self, k, v)
            self.__tensors__[k] = v


class TensorsOutputs(ModelOutputs, TensorsMixin):
    def __post_init__(self):
        self.__tensors__ = {}
        for f in fields(self):
            self.__tensors__[f.name] = getattr(self, f.name)
        super().__post_init__()


class TensorsTargets(ModelTargets, TensorsMixin):
    def __post_init__(self):
        self.__tensors__ = {}
        for f in fields(self):
            self.__tensors__[f.name] = getattr(self, f.name)
        super().__post_init__()


@dataclass(init=False)
class ListTensorsInputs(ModelInputs, ListTensorsMixin):
    def __init__(self, inputs: Optional[Dict] = dict(), **kwargs):
        self.__list_tensors__ = {}
        for k, v in {**inputs, **kwargs}.items():
            if not hasattr(self, k):
                setattr(self, k, v)
            self.__list_tensors__[k] = v
        super().__post_init__()

    def __post_init__(self):
        for f in fields(self):
            self.__list_tensors__[f.name] = getattr(self, f.name)
        super().__post_init__()


class ListTensorsOutputs(ModelOutputs, ListTensorsMixin):
    def __post_init__(self):
        self.__list_tensors__ = {}
        for f in fields(self):
            self.__list_tensors__[f.name] = getattr(self, f.name)
        super().__post_init__()


class ListTensorsTargets(ModelTargets, ListTensorsMixin):
    def __post_init__(self):
        self.__list_tensors__ = {}
        for f in fields(self):
            self.__list_tensors__[f.name] = getattr(self, f.name)
        super().__post_init__()


class CombineTensorsInputs(ModelInputs, CombineTensorsMixin):
    def __init__(
        self,
        dict_of_tensors: Dict[str, torch.Tensor] = dict(),
        dict_of_list_tensors: Dict[str, List[torch.Tensor]] = dict(),
    ):
        CombineTensorsMixin.__init__(
            self,
            dict_of_tensors=dict_of_tensors,
            dict_of_list_tensors=dict_of_list_tensors,
        )


class CombineTensorsOutputs(ModelOutputs, CombineTensorsMixin):
    def __init__(
        self,
        dict_of_tensors: Dict[str, torch.Tensor] = dict(),
        dict_of_list_tensors: Dict[str, List[torch.Tensor]] = dict(),
    ):
        CombineTensorsMixin.__init__(
            self,
            dict_of_tensors=dict_of_tensors,
            dict_of_list_tensors=dict_of_list_tensors,
        )


class CombineTensorsTargets(ModelTargets, CombineTensorsMixin):
    def __init__(
        self,
        dict_of_tensors: Dict[str, torch.Tensor] = dict(),
        dict_of_list_tensors: Dict[str, List[torch.Tensor]] = dict(),
    ):
        CombineTensorsMixin.__init__(
            self,
            dict_of_tensors=dict_of_tensors,
            dict_of_list_tensors=dict_of_list_tensors,
        )


@dataclass
class LossOutputs(TensorsOutputs):
    loss: torch.Tensor
