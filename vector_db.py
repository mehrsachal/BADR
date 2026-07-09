"""Simple local Vector Database (TF-IDF) for RAG without external dependencies."""
import os
import json
import math
import re
from collections import Counter


def tokenize(text):
    """Simple alphanumeric tokenizer."""
    return re.findall(r'\w+', text.lower())


class VectorDB:
    def __init__(self, db_path):
        self.db_path = db_path
        self.documents = []
        self.idf = {}

    def build_index(self, kb_dir):
        """Build the TF-IDF index from markdown files in kb_dir."""
        self.documents = []
        doc_id = 0

        for root, _, files in os.walk(kb_dir):
            for f in files:
                if f.lower().endswith('.md') and f.lower() != "lessons.md":
                    path = os.path.join(root, f)
                    try:
                        with open(path, 'r', encoding='utf-8') as f_in:
                            content = f_in.read()
                            # Chunk by empty lines (paragraphs)
                            chunks = [c.strip() for c in content.split('\n\n') if len(c.strip()) > 40]
                            if not chunks:
                                chunks = [content.strip()]
                            
                            for chunk in chunks:
                                if not chunk:
                                    continue
                                self.documents.append({
                                    'id': doc_id,
                                    'file': f,
                                    'text': chunk,
                                    'tokens': tokenize(chunk)
                                })
                                doc_id += 1
                    except Exception:
                        pass

        # Compute IDF
        N = len(self.documents)
        df = Counter()
        for doc in self.documents:
            unique_tokens = set(doc['tokens'])
            for token in unique_tokens:
                df[token] += 1

        self.idf = {token: math.log(N / (df[token] + 1)) + 1 for token in df}

        # Precompute document vector magnitudes
        for doc in self.documents:
            tf = Counter(doc['tokens'])
            vec_len = sum((tf[t] * self.idf.get(t, 0))**2 for t in tf)
            doc['magnitude'] = math.sqrt(vec_len) if vec_len > 0 else 1.0

        # Save to JSON
        data = {
            'documents': self.documents,
            'idf': self.idf
        }
        with open(self.db_path, 'w', encoding='utf-8') as f_out:
            json.dump(data, f_out)

        return len(self.documents)

    def load_index(self):
        """Load index from disk."""
        if not os.path.exists(self.db_path):
            return False
        try:
            with open(self.db_path, 'r', encoding='utf-8') as f_in:
                data = json.load(f_in)
                self.documents = data.get('documents', [])
                self.idf = data.get('idf', {})
            return True
        except Exception:
            return False

    def query(self, text, top_k=3):
        """Query the index and return top-k chunks."""
        if not self.documents:
            if not self.load_index():
                return []

        tokens = tokenize(text)
        if not tokens:
            return []

        tf_query = Counter(tokens)
        query_mag = math.sqrt(sum((tf_query[t] * self.idf.get(t, 0))**2 for t in tf_query))
        if query_mag == 0:
            return []

        scores = []
        for doc in self.documents:
            tf_doc = Counter(doc['tokens'])
            dot_product = sum(tf_query[t] * tf_doc.get(t, 0) * (self.idf.get(t, 0)**2) for t in tf_query)
            sim = dot_product / (query_mag * doc['magnitude'])
            scores.append((sim, doc))

        scores.sort(key=lambda x: x[0], reverse=True)
        # Filter for positive similarity
        return [doc for sim, doc in scores[:top_k] if sim > 0.05]
