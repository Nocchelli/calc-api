from abc import ABC, abstractmethod

class ExtratorBase(ABC):
    @abstractmethod
    def extrair(self, conteudo_arquivo: bytes) -> dict: # Mudou de list para dict
        pass