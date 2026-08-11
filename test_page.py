from onenote.writer import OneNoteWriter


brain = {
    "id": "test_reel_001",

    "status": "downloaded",

    "source": {
        "platform": "instagram",
        "url": "https://www.instagram.com/reel/test_reel_001/",
        "shortcode": "test_reel_001"
    },

    "creator": {
        "username": "test_creator",
        "full_name": "Test Creator",
        "verified": None,
        "followers": None
    },

    "metrics": {
        "views": None,
        "plays": None
    },

    "content": {
        "caption": "Test reel for InstaBrain OneNote integration.",
        "transcript": None,
        "vision_analysis": None
    },

    "media": {
        "video_path": None,
        "thumbnail_path": None,
        "duration": None
    },

    "knowledge": {
        "title": "Test OneNote Page",
        "summary": "This is a test page created by InstaBrain to verify the OneNote integration.",
        "main_topic": "OneNote integration",
        "difficulty": "Beginner",

        "key_concepts": [
            "Microsoft Graph",
            "OneNote API",
            "Brain Objects"
        ],

        "resources": [],

        "tools": [
            "Microsoft Graph",
            "MSAL"
        ],

        "code_snippets": [],

        "best_practices": [
            "Keep OneNote pages structured and readable."
        ],

        "mistakes_to_avoid": [
            "Creating duplicate sections."
        ],

        "action_items": [
            "Verify the generated page in OneNote."
        ],

        "tags": [
            "InstaBrain",
            "OneNote",
            "Microsoft Graph"
        ],

        "category": "Programming"
    },

    "timestamps": {
        "reel_created": "20260811"
    }
}

writer = OneNoteWriter()
writer.write(brain)

print("✅ Test Brain Object successfully written to OneNote.")
