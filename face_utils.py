import cv2
import numpy as np
import base64
from deepface import DeepFace
import tempfile
import os

def decode_image(base64_string):
    """Decodes a base64 string into an OpenCV image."""
    try:
        # Remove header if present
        if "," in base64_string:
            base64_string = base64_string.split(",")[1]
        
        encoded_data = base64.b64decode(base64_string)
        nparr = np.frombuffer(encoded_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        print(f"Error decoding image: {e}")
        return None

def get_face_embedding(img_base64):
    """Generates a face embedding from a base64 image string."""
    try:
        # Save to temp file for DeepFace
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp:
            img = decode_image(img_base64)
            if img is None:
                return None
            cv2.imwrite(temp.name, img)
            temp_path = temp.name

        # Generate embedding
        results = DeepFace.represent(
            img_path=temp_path, 
            model_name="VGG-Face", # Fast and reliable
            detector_backend="retinaface", # Accurate detection
            enforce_detection=True
        )
        
        os.unlink(temp_path)
        
        if results and len(results) > 0:
            return results[0]["embedding"]
        return None
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return None

def verify_face(img_base64, stored_embedding, threshold=0.40):
    """Verifies a face against a stored embedding using cosine similarity."""
    new_embedding = get_face_embedding(img_base64)
    if new_embedding is None:
        return False, 0.0
    
    # Simple cosine similarity manual calculation or use DeepFace.verify
    # DeepFace.verify is easier as it handles scaling
    # However, since we store only embeddings, we'll do manual cosine similarity
    
    a = np.array(new_embedding)
    b = np.array(stored_embedding)
    
    cos_sim = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    distance = 1 - cos_sim
    
    return distance <= threshold, distance

def compare_faces(embedding1, embedding2, threshold=0.40):
    """Compares two embeddings and returns True if they match."""
    if embedding1 is None or embedding2 is None:
        return False
        
    a = np.array(embedding1)
    b = np.array(embedding2)
    
    cos_sim = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    distance = 1 - cos_sim
    
    return distance <= threshold
