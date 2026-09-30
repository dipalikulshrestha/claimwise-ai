# ClaimWise AI — Product Overview

## Product Purpose

ClaimWise AI is a multimodal AI-powered insurance claims assistant.

The application helps insurance organizations process motor insurance claims by analyzing multiple forms of claim evidence and presenting a structured preliminary assessment to a human claims assessor.

## Problem

Insurance claims often require assessors to review multiple unstructured evidence sources:

- Accident videos
- Accident photographs
- Audio descriptions
- Insurance policy documents
- Claimant-provided information

Reviewing these sources manually can be time-consuming and inconsistent.

ClaimWise AI brings these evidence sources together and uses AI to extract, correlate and summarize relevant information.

## Target Users

Primary users:

- Insurance claims assessors
- Claims operations teams

Secondary users:

- Claimants submitting evidence
- Insurance technology/platform teams

## Core Capabilities

The product will eventually support:

1. Claim creation
2. Secure evidence upload
3. Evidence validation
4. Audio transcription
5. Policy document extraction
6. Image analysis
7. Video analysis
8. Multimodal AI reasoning
9. Policy and evidence correlation
10. Missing-information detection
11. Preliminary claim assessment
12. Human review
13. Audit trail

## Human-in-the-Loop Principle

ClaimWise AI is an assistant, not an autonomous claims decision maker.

AI-generated assessments must be presented as recommendations or evidence summaries for a human claims assessor.

The system must not autonomously approve or reject an insurance claim.

## AI Principles

AI outputs should:

- Be grounded in available evidence
- Clearly distinguish facts from inference
- Identify missing information
- Provide evidence references where practical
- Avoid fabricating information
- Flag uncertainty
- Require human review for consequential decisions

## Security and Privacy

Claim evidence may contain sensitive customer information.

The system must:

- Protect uploaded evidence
- Apply least-privilege access
- Encrypt sensitive data
- Avoid exposing sensitive information in logs
- Validate uploaded files
- Protect AI workflows from malicious or untrusted inputs

## Project Goal

This project is also a demonstration of Kiro's agentic software development capabilities.

Kiro will be used throughout the lifecycle for:

- Steering
- Specifications
- Agentic implementation
- Hooks
- MCP
- Custom agents
- Skills
- Bugfix specifications
- Parallel task execution
- Testing
- Code quality
- Development automation