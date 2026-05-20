from sentence_transformers import SentenceTransformer

class Embedder:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
        
    def __init__(self):
        # We load the model once into memory
        self.model = SentenceTransformer("BAAI/bge-small-en-v1.5")
        
    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """
        Takes a list of strings and returns a list of embedding vectors (384 dims each).
        """
        # encode() returns a numpy array, we convert to nested lists
        embeddings = self.model.encode(texts)
        return embeddings.tolist()
