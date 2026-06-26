"""Base classes for MBDataflow extractors."""

from __future__ import annotations

from abc import ABC, abstractmethod 
from typing import Any


class Extractor(ABC):
    """Abstract base extractor with scrape/(applicable to other forms of extraction) steps."""

    name: str

    @abstractmethod
    def scrape(self) -> Any:
        """Fetch raw data from the external source."""

    #@abstractmethod
    #def transform(self, raw_data: Any):
    #    """Transform raw data into a structure ready for loading."""

    #@abstractmethod
    #def load(self, transformed_data):
    #    """Load the transformed data into its destination."""

    def run(self) -> None:
        """Execute the extraction (in general) steps."""
        return self.scrape()
        #raw_data = self.scrape()
        #transformed = self.transform(raw_data)
        #self.load(transformed)
