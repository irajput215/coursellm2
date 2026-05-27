from rag.ingestion.parser import parse_document
import os

def test():
    print("Testing TXT Parser...")
    with open("test.txt", "w") as f:
        f.write("This is a raw text test.")
        
    res = parse_document("test.txt")
    print(res)
    os.remove("test.txt")
    
if __name__ == "__main__":
    test()
