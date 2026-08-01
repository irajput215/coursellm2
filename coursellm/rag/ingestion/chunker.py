import re

class SemanticChunker:
    def __init__(self, max_chunk_size: int = 1000, overlap: int = 100):
        self.max_chunk_size = max_chunk_size
        self.overlap = overlap

    def chunk_document(self, pages_data: list[dict], document_id: int, course_id: int, user_id: int) -> list[dict]:
        """
        Takes page data and splits it into semantic chunks with metadata.
        """
        chunks = []
        chunk_index = 0
        
        for page_info in pages_data:
            page_num = page_info["page"]
            text = page_info["text"]
            
            # Simple semantic split: split by paragraphs (double newline)
            paragraphs = re.split(r'\n\s*\n', text)
            
            current_chunk_text = ""
            
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                    
                if len(current_chunk_text) + len(para) <= self.max_chunk_size:
                    current_chunk_text += para + "\n\n"
                else:
                    if current_chunk_text.strip():
                        chunks.append(self._create_chunk_dict(
                            current_chunk_text.strip(), page_num, chunk_index, document_id, course_id, user_id
                        ))
                        chunk_index += 1
                        
                    if len(para) > self.max_chunk_size:
                        # Hard split by characters if paragraph is ridiculously long
                        for i in range(0, len(para), self.max_chunk_size - self.overlap):
                            piece = para[i:i+self.max_chunk_size]
                            chunks.append(self._create_chunk_dict(
                                piece, page_num, chunk_index, document_id, course_id, user_id
                            ))
                            chunk_index += 1
                        current_chunk_text = ""
                    else:
                        current_chunk_text = para + "\n\n"
                        
            # Add any remaining text
            if current_chunk_text.strip():
                chunks.append(self._create_chunk_dict(
                    current_chunk_text.strip(), page_num, chunk_index, document_id, course_id, user_id
                ))
                chunk_index += 1
                
        return chunks

    def _create_chunk_dict(self, content: str, page: int, chunk_index: int, document_id: int, course_id: int, user_id: int) -> dict:
        return {
            "content": content,
            "page": page,
            "topic": None, 
            "chunk_index": chunk_index,
            "document_id": document_id,
            "course_id": course_id,
            "user_id": user_id
        }
