"""
onenote/formatter.py
--------------------
Modular, category-aware HTML renderer for Microsoft OneNote.
Converts structured Brain Objects into rich, beautifully styled OneNote notes
with inline CSS, visual callout cards, step-by-step instructions, code blocks,
metadata headers, and source references.
"""

from __future__ import annotations

import os
from datetime import datetime
from html import escape
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# Curated harmonious color palettes for each category
CATEGORY_THEMES: dict[str, dict[str, str]] = {
    "Programming": {"theme": "#0078D4", "bg": "#F0F6FF", "border": "#CCE4FF"},
    "AI": {"theme": "#5C2D91", "bg": "#F6F0FD", "border": "#E2CEF7"},
    "Food": {"theme": "#D83B01", "bg": "#FFF4ED", "border": "#FDC7A6"},
    "Photography": {"theme": "#008272", "bg": "#EDFAF8", "border": "#A2E5DC"},
    "Gym": {"theme": "#C41C2E", "bg": "#FDF0F2", "border": "#F8B6BD"},
    "Finance": {"theme": "#107C41", "bg": "#EFFBF3", "border": "#A9E4BE"},
    "Travel": {"theme": "#00B7C3", "bg": "#EEFBFC", "border": "#97EDF2"},
    "Movies & Edits": {"theme": "#EA005E", "bg": "#FDF0F6", "border": "#F8BBD6"},
    "Productivity": {"theme": "#D18700", "bg": "#FEF9EE", "border": "#FCE2A6"},
    "Other": {"theme": "#6B69D6", "bg": "#F4F3FC", "border": "#D2D0F5"},
}

DEFAULT_THEME = {"theme": "#0078D4", "bg": "#F0F6FF", "border": "#CCE4FF"}


def format_date(value: Any) -> str:
    if not value:
        return ""
    try:
        val_str = str(value).strip()
        if val_str.isdigit() and len(val_str) == 8:
            return datetime.strptime(val_str, "%Y%m%d").strftime("%d %b %Y")
        return datetime.fromisoformat(val_str).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return str(value)


def format_views(count: Any) -> str:
    if count is None:
        return ""
    try:
        num = int(count)
        if num >= 1_000_000:
            return f"{num / 1_000_000:.1f}M"
        if num >= 1_000:
            return f"{num / 1_000:.1f}K"
        return str(num)
    except (TypeError, ValueError):
        return str(count)


def clean_url(value: str) -> str:
    if not value:
        return ""
    parts = urlsplit(str(value).strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _get_theme(category: str | None) -> dict[str, str]:
    if not category:
        return DEFAULT_THEME
    return CATEGORY_THEMES.get(category, DEFAULT_THEME)


def _build_hero_header(parts: list[str], brain: dict, theme: dict[str, str]) -> None:
    knowledge = brain.get("knowledge") or {}
    source = brain.get("source") or {}
    creator = brain.get("creator") or {}
    metrics = brain.get("metrics") or {}
    media = brain.get("media") or {}
    timestamps = brain.get("timestamps") or {}

    category = knowledge.get("category") or "General"
    title = knowledge.get("title") or source.get("shortcode") or "InstaBrain Reel"

    theme_color = theme["theme"]

    # 1. Category Badge
    parts.append(
        f'<div style="margin-bottom: 6px;">'
        f'<span style="display: inline-block; background-color: {theme_color}; color: #ffffff; '
        f'font-weight: bold; font-size: 9pt; padding: 3px 9px; border-radius: 12px; '
        f'text-transform: uppercase; letter-spacing: 0.5px;">{escape(category)}</span>'
        f'</div>'
    )

    # 2. Main Title
    parts.append(
        f'<h1 style="font-size: 18pt; color: #201F1E; margin-top: 4px; margin-bottom: 8px; line-height: 1.25;">'
        f'{escape(title)}'
        f'</h1>'
    )

    # 3. Metadata strip
    meta_items: list[str] = []

    username = creator.get("username")
    if username:
        clean_user = str(username).lstrip("@").strip()
        meta_items.append(f'👤 <strong>@{escape(clean_user)}</strong>')

    views = metrics.get("views") or metrics.get("plays")
    if views:
        formatted_views_str = format_views(views)
        if formatted_views_str:
            meta_items.append(f'👁️ {escape(formatted_views_str)} views')

    duration = media.get("duration")
    if duration:
        try:
            dur_sec = int(round(float(duration)))
            meta_items.append(f'⏱️ {dur_sec}s')
        except (ValueError, TypeError):
            pass

    reel_created = timestamps.get("reel_created")
    if reel_created:
        date_str = format_date(reel_created)
        if date_str:
            meta_items.append(f'📅 {escape(date_str)}')

    source_url = source.get("url")
    if source_url:
        cleaned_source = clean_url(source_url)
        if cleaned_source:
            meta_items.append(
                f'<a href="{escape(cleaned_source)}" style="color: {theme_color}; text-decoration: none; font-weight: bold;">🎬 Open Reel</a>'
            )

    if meta_items:
        joined_meta = " &nbsp;•&nbsp; ".join(meta_items)
        parts.append(
            f'<p style="font-size: 9.5pt; color: #605E5C; margin-top: 0; margin-bottom: 16px; line-height: 1.5;">'
            f'{joined_meta}'
            f'</p>'
        )


def _build_thumbnail_section(parts: list[str], brain: dict, include_thumbnail: bool) -> None:
    if not include_thumbnail:
        return

    media = brain.get("media") or {}
    thumb_path = media.get("thumbnail_path")

    # If thumbnail exists as a file or URL, render the container
    if thumb_path:
        parts.append(
            f'<div style="margin-bottom: 16px;">'
            f'<img src="name:thumbnail" alt="Reel Thumbnail" style="max-width: 480px; max-height: 360px; border-radius: 8px; border: 1px solid #EDEBE9;" />'
            f'</div>'
        )


def _build_summary_card(parts: list[str], summary: str | None, theme: dict[str, str]) -> None:
    if not summary or not str(summary).strip():
        return

    theme_color = theme["theme"]
    bg_color = theme["bg"]

    parts.append(
        f'<div style="background-color: {bg_color}; border-left: 4px solid {theme_color}; '
        f'padding: 12px 16px; border-radius: 0 6px 6px 0; margin-bottom: 18px;">'
        f'<h2 style="font-size: 11pt; color: {theme_color}; margin: 0 0 6px 0; font-weight: bold;">Summary</h2>'
        f'<p style="font-size: 10.5pt; color: #323130; margin: 0; line-height: 1.5;">{escape(str(summary).strip())}</p>'
        f'</div>'
    )


def _build_highlights_bar(parts: list[str], items: list[tuple[str, Any]]) -> None:
    valid_items = [(label, str(val).strip()) for label, val in items if val and str(val).strip()]
    if not valid_items:
        return

    badges = []
    for label, val in valid_items:
        badges.append(
            f'<span style="display: inline-block; background: #F3F2F1; border: 1px solid #EDEBE9; '
            f'color: #323130; padding: 4px 10px; border-radius: 4px; font-size: 9.5pt; margin-right: 8px; margin-bottom: 6px;">'
            f'<strong>{escape(label)}:</strong> {escape(val)}'
            f'</span>'
        )

    parts.append(f'<div style="margin-bottom: 16px;">{"".join(badges)}</div>')


def _build_steps_section(parts: list[str], heading: str, steps: Any) -> None:
    if not steps:
        return

    step_items: list[str] = []
    if isinstance(steps, list):
        step_items = [str(s).strip() for s in steps if s and str(s).strip()]
    elif isinstance(steps, str):
        step_items = [s.strip() for s in steps.splitlines() if s.strip()]

    if not step_items:
        return

    parts.append(
        f'<h2 style="font-size: 12pt; color: #201F1E; border-bottom: 1px solid #EDEBE9; '
        f'padding-bottom: 4px; margin-top: 20px; margin-bottom: 10px;">{escape(heading)}</h2>'
    )
    parts.append('<ol style="margin-left: 20px; padding-left: 0; font-size: 10.5pt; color: #323130; line-height: 1.6;">')
    for step in step_items:
        parts.append(f'<li style="margin-bottom: 6px;">{escape(step)}</li>')
    parts.append('</ol>')


def _build_code_section(parts: list[str], heading: str, code_snippets: Any) -> None:
    if not code_snippets:
        return

    snippets: list[str] = []
    if isinstance(code_snippets, list):
        snippets = [str(c).strip() for c in code_snippets if c and str(c).strip()]
    elif isinstance(code_snippets, str):
        snippets = [code_snippets.strip()] if code_snippets.strip() else []

    if not snippets:
        return

    parts.append(
        f'<h2 style="font-size: 12pt; color: #201F1E; border-bottom: 1px solid #EDEBE9; '
        f'padding-bottom: 4px; margin-top: 20px; margin-bottom: 10px;">{escape(heading)}</h2>'
    )

    for snippet in snippets:
        parts.append(
            f'<pre style="background: #1E1E1E; color: #DCDCDC; padding: 12px 14px; border-radius: 6px; '
            f'font-family: Consolas, \'Courier New\', monospace; font-size: 10pt; line-height: 1.45; '
            f'overflow-x: auto; margin-bottom: 12px; white-space: pre-wrap;"><code>{escape(snippet)}</code></pre>'
        )


def _build_ingredients_table(parts: list[str], ingredients: Any, measurements: Any, cookware: Any) -> None:
    ing_list = [str(i).strip() for i in ingredients] if isinstance(ingredients, list) else []
    meas_list = [str(m).strip() for m in measurements] if isinstance(measurements, list) else []

    if not ing_list and not meas_list and not cookware:
        return

    parts.append(
        f'<h2 style="font-size: 12pt; color: #201F1E; border-bottom: 1px solid #EDEBE9; '
        f'padding-bottom: 4px; margin-top: 20px; margin-bottom: 10px;">Ingredients & Equipment</h2>'
    )

    if cookware:
        cookware_str = ", ".join(cookware) if isinstance(cookware, list) else str(cookware)
        parts.append(
            f'<p style="font-size: 10pt; color: #605E5C; margin-bottom: 10px;">'
            f'🍳 <strong>Cookware / Tools:</strong> {escape(cookware_str)}'
            f'</p>'
        )

    if ing_list:
        parts.append('<table style="width: 100%; max-width: 550px; border-collapse: collapse; margin-bottom: 16px; font-size: 10pt;">')
        parts.append('<thead><tr style="background: #F3F2F1; border-bottom: 2px solid #EDEBE9;"><th style="text-align: left; padding: 6px 10px; color: #323130;">Ingredient</th><th style="text-align: left; padding: 6px 10px; color: #323130;">Measurement / Notes</th></tr></thead>')
        parts.append('<tbody>')

        for idx, ing in enumerate(ing_list):
            meas = meas_list[idx] if idx < len(meas_list) else ""
            bg = "#FAFAFA" if idx % 2 == 1 else "#FFFFFF"
            parts.append(
                f'<tr style="background: {bg}; border-bottom: 1px solid #EDEBE9;">'
                f'<td style="padding: 6px 10px; color: #323130;"><strong>{escape(ing)}</strong></td>'
                f'<td style="padding: 6px 10px; color: #605E5C;">{escape(meas)}</td>'
                f'</tr>'
            )

        parts.append('</tbody></table>')


def _build_list_section(parts: list[str], heading: str, items: Any) -> None:
    if not items:
        return

    clean_items: list[str] = []
    if isinstance(items, list):
        clean_items = [str(i).strip() for i in items if i is not None and str(i).strip()]
    elif isinstance(items, str):
        clean_items = [str(items).strip()] if str(items).strip() else []

    if not clean_items:
        return

    parts.append(
        f'<h2 style="font-size: 12pt; color: #201F1E; border-bottom: 1px solid #EDEBE9; '
        f'padding-bottom: 4px; margin-top: 20px; margin-bottom: 10px;">{escape(heading)}</h2>'
    )
    parts.append('<ul style="margin-left: 20px; padding-left: 0; font-size: 10.5pt; color: #323130; line-height: 1.6;">')
    for item in clean_items:
        parts.append(f'<li style="margin-bottom: 4px;">{escape(item)}</li>')
    parts.append('</ul>')


def _build_resources_section(parts: list[str], heading: str, resources: Any, theme: dict[str, str]) -> None:
    if not resources:
        return

    valid_res: list[tuple[str, str]] = []
    if isinstance(resources, list):
        for res in resources:
            if isinstance(res, dict):
                name = str(res.get("name") or "").strip()
                url = str(res.get("url") or "").strip()
                if name or url:
                    valid_res.append((name or url, url))
            elif isinstance(res, str) and res.strip():
                valid_res.append((res.strip(), res.strip()))
    elif isinstance(resources, str) and resources.strip():
        valid_res.append((resources.strip(), resources.strip()))

    if not valid_res:
        return

    theme_color = theme["theme"]
    parts.append(
        f'<h2 style="font-size: 12pt; color: #201F1E; border-bottom: 1px solid #EDEBE9; '
        f'padding-bottom: 4px; margin-top: 20px; margin-bottom: 10px;">{escape(heading)}</h2>'
    )
    parts.append('<ul style="margin-left: 20px; padding-left: 0; font-size: 10.5pt; color: #323130; line-height: 1.6;">')
    for name, url in valid_res:
        if url.startswith("http://") or url.startswith("https://"):
            parts.append(f'<li style="margin-bottom: 4px;"><a href="{escape(url)}" style="color: {theme_color}; text-decoration: underline;">{escape(name)}</a></li>')
        else:
            parts.append(f'<li style="margin-bottom: 4px;">{escape(name)}</li>')
    parts.append('</ul>')


def _build_tag_chips(parts: list[str], tags: Any) -> None:
    if not tags:
        return

    tag_list = [str(t).strip().lstrip("#") for t in tags if t and str(t).strip()] if isinstance(tags, list) else []
    if not tag_list:
        return

    chips = [
        f'<span style="display: inline-block; background: #F3F2F1; color: #605E5C; '
        f'padding: 3px 9px; border-radius: 12px; font-size: 9pt; margin-right: 6px; margin-bottom: 4px;">'
        f'#{escape(t)}</span>'
        for t in tag_list
    ]

    parts.append(
        f'<div style="margin-top: 22px; margin-bottom: 12px;">'
        f'{"".join(chips)}'
        f'</div>'
    )


def _build_vision_section(parts: list[str], vision_analysis: Any, theme: dict[str, str]) -> None:
    if not vision_analysis or not isinstance(vision_analysis, dict):
        return

    on_screen_text = vision_analysis.get("on_screen_text") or []
    visible_steps = vision_analysis.get("visible_steps") or []
    visual_facts = vision_analysis.get("visual_facts") or []

    ost_filtered = [str(t).strip() for t in on_screen_text if str(t).strip()]
    steps_filtered = [str(s).strip() for s in visible_steps if str(s).strip()]
    facts_filtered = [str(f).strip() for f in visual_facts if str(f).strip()]

    if not ost_filtered and not steps_filtered and not facts_filtered:
        return

    theme_color = theme["theme"]

    parts.append(
        f'<div style="background: #FAFAFA; border: 1px solid #EDEBE9; border-radius: 6px; padding: 12px 16px; margin-top: 24px; margin-bottom: 16px;">'
        f'<h2 style="font-size: 11pt; color: {theme_color}; margin: 0 0 8px 0; font-weight: bold;">👁️ Visual Observations (from Video Frames)</h2>'
    )

    if ost_filtered:
        parts.append('<p style="font-size: 9.5pt; color: #605E5C; margin: 6px 0 2px 0;"><strong>On-Screen Text Detected:</strong></p>')
        parts.append('<ul style="margin-left: 18px; margin-top: 2px; margin-bottom: 6px; font-size: 9.5pt; color: #323130;">')
        for item in ost_filtered:
            parts.append(f'<li>{escape(item)}</li>')
        parts.append('</ul>')

    if steps_filtered:
        parts.append('<p style="font-size: 9.5pt; color: #605E5C; margin: 6px 0 2px 0;"><strong>Visible Actions Shown:</strong></p>')
        parts.append('<ul style="margin-left: 18px; margin-top: 2px; margin-bottom: 6px; font-size: 9.5pt; color: #323130;">')
        for item in steps_filtered:
            parts.append(f'<li>{escape(item)}</li>')
        parts.append('</ul>')

    if facts_filtered:
        parts.append('<p style="font-size: 9.5pt; color: #605E5C; margin: 6px 0 2px 0;"><strong>Visual Facts:</strong></p>')
        parts.append('<ul style="margin-left: 18px; margin-top: 2px; margin-bottom: 6px; font-size: 9.5pt; color: #323130;">')
        for item in facts_filtered:
            parts.append(f'<li>{escape(item)}</li>')
        parts.append('</ul>')

    parts.append('</div>')


def _build_source_reference(parts: list[str], caption: str | None, transcript: str | None) -> None:
    has_caption = bool(caption and str(caption).strip())
    has_transcript = bool(transcript and str(transcript).strip())

    if not has_caption and not has_transcript:
        return

    parts.append('<hr style="border: none; border-top: 1px solid #EDEBE9; margin-top: 28px; margin-bottom: 18px;" />')
    parts.append('<h2 style="font-size: 11pt; color: #605E5C; margin-bottom: 10px; font-weight: bold;">📖 Source Evidence & Reference</h2>')

    if has_caption:
        parts.append('<h3 style="font-size: 10pt; color: #605E5C; margin: 8px 0 4px 0;">💬 Original Caption</h3>')
        parts.append(
            f'<blockquote style="background: #FAF9F8; border-left: 3px solid #C8C6C4; margin: 0 0 14px 0; '
            f'padding: 8px 12px; font-size: 9pt; color: #605E5C; line-height: 1.45; white-space: pre-wrap;">'
            f'{escape(str(caption).strip())}'
            f'</blockquote>'
        )

    if has_transcript:
        parts.append('<h3 style="font-size: 10pt; color: #605E5C; margin: 8px 0 4px 0;">🎙️ Whisper Audio Transcript</h3>')
        parts.append(
            f'<blockquote style="background: #FAF9F8; border-left: 3px solid #C8C6C4; margin: 0 0 14px 0; '
            f'padding: 8px 12px; font-size: 9pt; color: #605E5C; line-height: 1.45; white-space: pre-wrap;">'
            f'{escape(str(transcript).strip())}'
            f'</blockquote>'
        )


def format_to_html(brain: dict, include_thumbnail: bool = True) -> str:
    """
    Convert a Brain Object into high-fidelity, styled OneNote-compatible HTML.
    """
    knowledge = brain.get("knowledge") or {}
    content = brain.get("content") or {}
    category = knowledge.get("category") or "Other"

    theme = _get_theme(category)
    parts: list[str] = ["<html>", "<body>"]

    # 1. Hero Header (Category badge, title, metadata bar)
    _build_hero_header(parts, brain, theme)

    # 2. Thumbnail
    _build_thumbnail_section(parts, brain, include_thumbnail)

    # 3. Summary Card
    _build_summary_card(parts, knowledge.get("summary"), theme)

    # Track handled keys so generic fallback doesn't duplicate them
    handled_keys = {
        "title", "summary", "category", "tags", "websites", "resources"
    }

    # 4. Category-Specific Highlights and Layouts
    if category == "Programming":
        highlights = [
            ("Difficulty", knowledge.get("difficulty")),
            ("Main Topic", knowledge.get("main_topic")),
        ]
        _build_highlights_bar(parts, highlights)
        handled_keys.update({"difficulty", "main_topic", "key_concepts", "code_snippets", "tools", "best_practices", "mistakes_to_avoid", "action_items"})

        _build_code_section(parts, "💻 Code Snippets", knowledge.get("code_snippets"))
        _build_list_section(parts, "Key Concepts", knowledge.get("key_concepts"))
        _build_list_section(parts, "Tools & Libraries", knowledge.get("tools"))
        _build_list_section(parts, "Best Practices", knowledge.get("best_practices"))
        _build_list_section(parts, "Mistakes to Avoid", knowledge.get("mistakes_to_avoid"))
        _build_list_section(parts, "Action Items", knowledge.get("action_items"))

    elif category == "AI":
        highlights = [
            ("Use Cases", ", ".join(knowledge.get("use_cases")) if isinstance(knowledge.get("use_cases"), list) else knowledge.get("use_cases")),
        ]
        _build_highlights_bar(parts, highlights)
        handled_keys.update({"models", "tools", "prompts", "concepts", "use_cases", "key_points", "tips"})

        _build_code_section(parts, "🤖 AI Prompts & Instructions", knowledge.get("prompts"))
        _build_list_section(parts, "Models & Frameworks", knowledge.get("models"))
        _build_list_section(parts, "AI Tools & Services", knowledge.get("tools"))
        _build_list_section(parts, "Core Concepts", knowledge.get("concepts"))
        _build_list_section(parts, "Key Points", knowledge.get("key_points"))
        _build_list_section(parts, "Tips & Best Practices", knowledge.get("tips"))

    elif category == "Food":
        highlights = [
            ("Cuisine", ", ".join(knowledge.get("cuisine")) if isinstance(knowledge.get("cuisine"), list) else knowledge.get("cuisine")),
            ("Dish", ", ".join(knowledge.get("dishes")) if isinstance(knowledge.get("dishes"), list) else knowledge.get("dishes")),
        ]
        _build_highlights_bar(parts, highlights)
        handled_keys.update({"dishes", "cuisine", "ingredients", "measurements", "cookware", "steps", "tips"})

        _build_ingredients_table(parts, knowledge.get("ingredients"), knowledge.get("measurements"), knowledge.get("cookware"))
        _build_steps_section(parts, "👨‍🍳 Step-by-Step Instructions", knowledge.get("steps"))
        _build_list_section(parts, "Cooking Tips & Notes", knowledge.get("tips"))

    elif category == "Photography":
        handled_keys.update({"camera_settings", "gear", "lighting", "techniques", "locations", "editing_tools", "tips"})

        _build_list_section(parts, "📷 Camera Settings", knowledge.get("camera_settings"))
        _build_list_section(parts, "Gear & Lenses", knowledge.get("gear"))
        _build_list_section(parts, "Lighting Setup", knowledge.get("lighting"))
        _build_list_section(parts, "Techniques & Composition", knowledge.get("techniques"))
        _build_list_section(parts, "Locations & Scenery", knowledge.get("locations"))
        _build_list_section(parts, "Editing Tools & Software", knowledge.get("editing_tools"))
        _build_list_section(parts, "Pro Tips", knowledge.get("tips"))

    elif category == "Gym":
        highlights = [
            ("Workout Type", ", ".join(knowledge.get("workout_type")) if isinstance(knowledge.get("workout_type"), list) else knowledge.get("workout_type")),
            ("Target Muscles", ", ".join(knowledge.get("muscles")) if isinstance(knowledge.get("muscles"), list) else knowledge.get("muscles")),
        ]
        _build_highlights_bar(parts, highlights)
        handled_keys.update({"workout_type", "muscles", "equipment", "exercises", "sets_reps", "form_cues", "tips"})

        _build_list_section(parts, "🏋️ Exercises & Routine", knowledge.get("exercises"))
        _build_list_section(parts, "Sets & Reps", knowledge.get("sets_reps"))
        _build_list_section(parts, "Form Cues & Execution", knowledge.get("form_cues"))
        _build_list_section(parts, "Equipment", knowledge.get("equipment"))
        _build_list_section(parts, "Training Tips", knowledge.get("tips"))

    elif category == "Movies & Edits":
        highlights = [
            ("Film / Show", ", ".join(knowledge.get("movies") or knowledge.get("shows") or [])),
            ("Music / Song", ", ".join(knowledge.get("songs") or [])),
        ]
        _build_highlights_bar(parts, highlights)
        handled_keys.update({"movies", "shows", "songs", "editing_apps", "transitions", "effects", "steps", "templates", "tips"})

        _build_list_section(parts, "Editing Apps & Software", knowledge.get("editing_apps"))
        _build_list_section(parts, "Transitions & Keyframes", knowledge.get("transitions"))
        _build_list_section(parts, "Effects & Color Grading", knowledge.get("effects"))
        _build_list_section(parts, "Templates & Presets", knowledge.get("templates"))
        _build_steps_section(parts, "Editing Workflow", knowledge.get("steps"))
        _build_list_section(parts, "Tips & Tricks", knowledge.get("tips"))

    elif category == "Travel":
        highlights = [
            ("Destinations", ", ".join(knowledge.get("destinations")) if isinstance(knowledge.get("destinations"), list) else knowledge.get("destinations")),
            ("Best Time to Visit", ", ".join(knowledge.get("best_time")) if isinstance(knowledge.get("best_time"), list) else knowledge.get("best_time")),
        ]
        _build_highlights_bar(parts, highlights)
        handled_keys.update({"destinations", "best_time", "hotels", "attractions", "restaurants", "transport", "budget_tips", "tips"})

        _build_list_section(parts, "Attractions & Sights", knowledge.get("attractions"))
        _build_list_section(parts, "Hotels & Stays", knowledge.get("hotels"))
        _build_list_section(parts, "Restaurants & Food", knowledge.get("restaurants"))
        _build_list_section(parts, "Transport & Getting Around", knowledge.get("transport"))
        _build_list_section(parts, "Budget Tips", knowledge.get("budget_tips"))
        _build_list_section(parts, "Travel Tips", knowledge.get("tips"))

    elif category == "Finance":
        handled_keys.update({"concepts", "stocks", "funds", "apps", "key_points", "numbers", "tips", "risks"})

        _build_list_section(parts, "Financial Concepts", knowledge.get("concepts"))
        _build_list_section(parts, "Stocks & Funds", knowledge.get("stocks") or knowledge.get("funds"))
        _build_list_section(parts, "Key Metrics & Numbers", knowledge.get("numbers"))
        _build_list_section(parts, "Key Points & Rules", knowledge.get("key_points"))
        _build_list_section(parts, "Risks & Pitfalls", knowledge.get("risks"))
        _build_list_section(parts, "Apps & Platforms", knowledge.get("apps"))
        _build_list_section(parts, "Actionable Tips", knowledge.get("tips"))

    elif category == "Productivity":
        handled_keys.update({"methods", "apps", "templates", "shortcuts", "workflows", "habits", "tips"})

        _build_list_section(parts, "Methods & Frameworks", knowledge.get("methods"))
        _build_list_section(parts, "Apps & Software", knowledge.get("apps"))
        _build_code_section(parts, "Shortcuts & Keybindings", knowledge.get("shortcuts"))
        _build_list_section(parts, "Workflows & Systems", knowledge.get("workflows"))
        _build_list_section(parts, "Daily Habits", knowledge.get("habits"))
        _build_list_section(parts, "Templates", knowledge.get("templates"))
        _build_list_section(parts, "Productivity Tips", knowledge.get("tips"))

    elif category == "Other":
        handled_keys.update({"key_points", "steps", "recommendations"})

        _build_list_section(parts, "Key Points", knowledge.get("key_points"))
        _build_steps_section(parts, "Action Steps", knowledge.get("steps"))
        _build_list_section(parts, "Recommendations", knowledge.get("recommendations"))

    # 5. Generic Fallback for any unhandled knowledge fields
    for k, v in knowledge.items():
        if k in handled_keys or not v:
            continue
        heading = k.replace("_", " ").title()
        if isinstance(v, list):
            _build_list_section(parts, heading, v)
        elif isinstance(v, str):
            _build_list_section(parts, heading, [v])

    # 6. Resources & Websites
    _build_resources_section(parts, "🔗 Resources & Links", knowledge.get("resources") or knowledge.get("websites"), theme)

    # 7. Tags
    _build_tag_chips(parts, knowledge.get("tags"))

    # 8. Visual Observations from Video Frames
    _build_vision_section(parts, content.get("vision_analysis"), theme)

    # 9. Source Reference (Original Caption & Whisper Transcript)
    _build_source_reference(parts, content.get("caption"), content.get("transcript"))

    parts.append("</body>")
    parts.append("</html>")

    return "\n".join(parts)


def format_brain_object(brain: dict, include_thumbnail: bool = True) -> str:
    """Public wrapper matching existing API signature."""
    return format_to_html(brain, include_thumbnail=include_thumbnail)
