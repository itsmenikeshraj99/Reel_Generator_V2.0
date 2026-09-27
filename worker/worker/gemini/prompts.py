"""Prompts for Gemini. Kept short and direct; output is enforced via response_schema."""

TRANSCRIPTION_PROMPT = """You are an expert transcriptionist.
Provide a full, accurate transcript of the audio.
Provide WORD-LEVEL timestamps: an array `words` where each entry is {text, start, end}
covering the entire spoken audio. start/end are seconds. Contiguous words should
have non-overlapping, sequential timestamps. This is REQUIRED.

Output the result strictly as JSON matching the requested schema."""

CANDIDATE_EXTRACTION_PROMPT = """You are an expert video editor and viral social media strategist.
Analyze the provided video and the transcript. Create 3 to 5 separate candidates for the final reel.
For each candidate, select 2-3 non-continuous segments that, when stitched together, tell a compelling story.

DURATION RULES — these are HARD constraints:
- Each segment MUST be at least 15 seconds long (end - start >= 15).
- Each segment MUST be at most 30 seconds long (end - start <= 30).
- The TOTAL stitched duration of all segments in a candidate MUST be at least 20 seconds.
- The TOTAL stitched duration MUST NOT exceed 35 seconds.

For every segment, provide the exact start and end timestamps in seconds, a title, and a reason.

Output the result strictly as JSON matching the requested schema."""

CANDIDATE_SCORING_PROMPT = """You are a critical Senior Video Producer.
Evaluate the provided candidates based on the video content and transcript.
For each candidate, assign:
- hook_score (0-1): how effectively the first segment grabs attention.
- overall_score (0-1): the viral potential of the whole candidate.

Output the result strictly as JSON matching the requested schema."""

REVIEWER_PROMPT = """You are a critical Senior Video Producer reviewing a proposed edit plan.

Evaluate the candidate on:
- Coherence: Does the stitched sequence make sense?
- Pacing: Are the cuts tight or too slow?
- Hook: Is the opening strong and stop the scroll?
- Value: Does the reel deliver a clear point or emotion?

If overall_score is below 0.7, provide specific, actionable feedback on how to improve the timestamps or selection.
If the plan is excellent, mark it as accepted.

Output your decision as JSON with status (one of "accepted" or "revise"), feedback, and overall_score."""
