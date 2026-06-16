"""Base classes for MBDataflow loaders."""

from __future__ import annotations

from abc import ABC, abstractmethod 
from typing import Any 


class Loader(ABC):
    """Abstract base loader with drive_loader/BQ_loader/(applicable to other forms of load) steps."""

    name: str

    @abstractmethod
    def load(self, transformed_data):
        """Load the transformed data into its destination."""

    def run(self) -> None:
        """Execute the extraction (in general) steps."""
        #transformed = self.transform(raw_data) needs the path of the .csv
        self.load()

# Still in construction
