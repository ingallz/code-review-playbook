from typing import List
from pydantic import BaseModel, Field


class InlineComment(BaseModel):
    """One review comment on a specific line of code."""
    lineContent: str = Field(description="The EXACT line from the diff including the leading '+' character")
    reviewComment: str = Field(description="GitHub Markdown review comment explaining the issue")
    category: str = Field(default="suggestion", description="One of: bug, security, performance, style, suggestion")


class AgentReviewResult(BaseModel):
    """Output from one review agent."""
    reviews: List[InlineComment] = Field(default_factory=list)
