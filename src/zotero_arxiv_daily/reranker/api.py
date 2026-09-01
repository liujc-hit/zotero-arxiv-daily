from .base import BaseReranker, register_reranker
from openai import OpenAI
import numpy as np
@register_reranker("api")
class ApiReranker(BaseReranker):
    def get_embeddings(self, texts: list[str]) -> np.ndarray:
        client = OpenAI(api_key=self.config.reranker.api.key, base_url=self.config.reranker.api.base_url)
        batch_size = self.config.reranker.api.get("batch_size") or 64
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = client.embeddings.create(
                input=batch,
                model=self.config.reranker.api.model
            )
            all_embeddings.extend([r.embedding for r in response.data])
        return np.array(all_embeddings)
