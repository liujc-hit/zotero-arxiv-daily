from __future__ import annotations

from .base import BaseReranker, register_reranker
import logging
import warnings
from typing import TYPE_CHECKING

import numpy as np
from omegaconf import DictConfig

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


@register_reranker("local")
class LocalReranker(BaseReranker):
    def __init__(self, config: DictConfig):
        super().__init__(config)
        self._encoder: SentenceTransformer | None = None
        if self.config.reranker.local.encode_kwargs:
            self._encode_kwargs = self.config.reranker.local.encode_kwargs
        else:
            self._encode_kwargs = {}

    def get_embeddings(self, texts: list[str]) -> np.ndarray:
        if self._encoder is None:
            if not self.config.executor.debug:
                from transformers.utils import logging as transformers_logging
                from huggingface_hub.utils import logging as hf_logging

                transformers_logging.set_verbosity_error()
                hf_logging.set_verbosity_error()
                logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
                logging.getLogger("sentence_transformers.SentenceTransformer").setLevel(logging.ERROR)
                logging.getLogger("transformers").setLevel(logging.ERROR)
                logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
                logging.getLogger("huggingface_hub.utils._http").setLevel(logging.ERROR)
                warnings.filterwarnings("ignore", category=FutureWarning)

            from sentence_transformers import SentenceTransformer

            self._encoder = SentenceTransformer(
                self.config.reranker.local.model, trust_remote_code=True
            )
        features = self._encoder.encode(
            texts, **self._encode_kwargs, show_progress_bar=True
        )
        return np.asarray(features)
