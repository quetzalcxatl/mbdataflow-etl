"""Base classes for MBDataflow transformers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Transformer(ABC):
    """Abstract base transformer with pre-processing/(applicable to other forms of transform) steps."""

    name: str

    @abstractmethod
    def transform(self, raw_data: Any):
        """Transform raw data into a structure ready for loading."""

    def run(self) -> None:
        """Execute the transform (in general) steps."""
        #raw_data = self.scrape()
        self.transform()

# Still in construction
        
