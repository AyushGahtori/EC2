from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SEOActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None

    action: str = Field(min_length=1)
    topic: str | None = None
    url: str | None = None
    title: str | None = None
    content: str | None = None
    query: str | None = None

    model_config = ConfigDict(extra="allow")


class SEOSearchInsights(BaseModel):
    primaryKeywords: list[str] = Field(default_factory=list)
    relatedKeywords: list[str] = Field(default_factory=list)
    relatedQuestions: list[str] = Field(default_factory=list)
    searchIntent: str = ""
    competitorAnalysis: str = ""
    aiOverviewSummary: str = ""
    topResultTitles: list[str] = Field(default_factory=list)
    topDomains: list[str] = Field(default_factory=list)


class SEOContentBrief(BaseModel):
    targetIntent: str = ""
    contentOutline: str = ""
    recommendedHeadings: list[str] = Field(default_factory=list)
    keyEntitiesToMention: list[str] = Field(default_factory=list)
    faqSuggestions: list[str] = Field(default_factory=list)
    keywordPlacementGuidance: str = ""
    contentStructureRecommendations: str = ""
    writingGuidelines: str = ""


class SEOArticleAudit(BaseModel):
    contentStrengths: str = ""
    contentGaps: str = ""
    keywordOpportunities: str = ""
    structureImprovements: str = ""
    e_e_a_t_assessment: str = ""
    missingSections: list[str] = Field(default_factory=list)
    prioritizedRecommendations: list[str] = Field(default_factory=list)


class SEOSectionEdits(BaseModel):
    improvedSections: list[dict[str, str]] = Field(default_factory=list)
    keywordIntegrationSummary: str = ""
    changesExplanation: str = ""


class SEOReportSection(BaseModel):
    title: str
    summary: str
    bullets: list[str] = Field(default_factory=list)
    kind: str = "section"


class SEOAnalysisResult(BaseModel):
    agentSlug: str = "seo-agent"
    displayName: str = "SEO Agent"
    mode: str = "brief"
    inputMode: str = "topic_only"
    topic: str = ""
    searchQuery: str = ""
    title: str | None = None
    sourceUrl: str | None = None
    extractedArticle: str | None = None
    sourceWordCount: int = 0
    sourceCharacterCount: int = 0
    warnings: list[str] = Field(default_factory=list)
    searchInsights: SEOSearchInsights = Field(default_factory=SEOSearchInsights)
    contentBrief: SEOContentBrief | None = None
    articleAudit: SEOArticleAudit | None = None
    sectionEdits: SEOSectionEdits | None = None
    reportSections: list[SEOReportSection] = Field(default_factory=list)
    nextSteps: list[str] = Field(default_factory=list)
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SEOActionResponse(BaseModel):
    status: str
    type: str
    message: str | None = None
    summary: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    displayName: str | None = None

