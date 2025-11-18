from abc import ABC, abstractmethod

class OcrService(ABC):
    @abstractmethod
    def detect_text(self, image_path):
        pass
