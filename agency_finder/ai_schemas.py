try:
    from pydantic import BaseModel, Field
    from typing import Optional, Literal
    _PYDANTIC_AVAILABLE = True

    class PortfolioHighlight(BaseModel):
        domain: str = Field(description="The domain of the client website")
        description: str = Field(description="One-line description of what the client does")

    class AIEnhancedReport(BaseModel):
        summary: str = Field(description="2-3 sentence company snapshot in Italian")
        company_profile: str = Field(description="1 paragraph formal company profile in Italian")
        services_grouped: dict[str, list[str]] = Field(description="Services clustered by category")
        portfolio_highlights: list[PortfolioHighlight] = Field(description="Top 5 portfolio entries with descriptions")
        key_strengths: list[str] = Field(description="3-5 key strengths")

    class AIApproach(BaseModel):
        recap: str = Field(description="1 paragraph: what the agency does")
        ideal_outreach_angle: str = Field(description="1-2 sentence recommended hook")
        suggested_first_message: str = Field(description="3-4 sentence Italian cold outreach opener")
        talking_points: list[str] = Field(description="3-5 specific talking points")
        red_flags: list[str] = Field(description="Any concerns or red flags")
        best_channel: Literal["email", "linkedin", "phone"] = Field(description="Best communication channel")
        best_channel_reason: str = Field(description="Why this channel is best")
        approach_tone: Literal["formal", "consultative", "partnership"] = Field(description="Recommended tone for outreach")

    class AIQueryResult(BaseModel):
        queries: list[str] = Field(description="3-5 optimized search queries")
except ImportError:
    _PYDANTIC_AVAILABLE = False

    class PortfolioHighlight:
        pass

    class AIEnhancedReport:
        pass

    class AIApproach:
        pass

    class AIQueryResult:
        pass
