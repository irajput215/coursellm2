import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel
from typing import List

class Embedder:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
        
    def __init__(self):
        # Use BGE-small model (384 dimensions, efficient)
        model_name = "BAAI/bge-small-en-v1.5"
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name)
        self.model.eval()
        
        # Move to GPU if available (optional for Mac MPS)
        if torch.backends.mps.is_available():
            self.model = self.model.to('mps')
        elif torch.cuda.is_available():
            self.model = self.model.to('cuda')
        
    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Takes a list of strings and returns a list of embedding vectors.
        BGE models use CLS pooling for embeddings.
        """
        # Tokenize with BGE's recommended settings
        encoded_input = self.tokenizer(
            texts, 
            padding=True, 
            truncation=True, 
            return_tensors='pt',
            max_length=512
        )
        
        # Move to same device as model
        device = next(self.model.parameters()).device
        encoded_input = {k: v.to(device) for k, v in encoded_input.items()}
        
        # Generate embeddings
        with torch.no_grad():
            model_output = self.model(**encoded_input)
        
        # CLS pooling (first token) - recommended for BGE models
        sentence_embeddings = model_output.last_hidden_state[:, 0, :]
        
        # Normalize embeddings
        sentence_embeddings = F.normalize(sentence_embeddings, p=2, dim=1)
        
        return sentence_embeddings.cpu().tolist()
    
    def generate_embedding_single(self, text: str) -> List[float]:
        """Convenience method for single text embedding"""
        return self.generate_embeddings([text])[0]
