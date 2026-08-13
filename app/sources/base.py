from abc import ABC, abstractmethod
from typing import List
from app.models import RawPost

class ScraperSource(ABC):
    @abstractmethod
    def search_posts(self, query: str, limit: int) -> List[RawPost]:
        pass
