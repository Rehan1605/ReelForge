def format_output(
    metadata,
    category,
    extraction
):
    return {
        "source_url": metadata["source_url"],
        "shortcode": metadata["shortcode"],
        "creator": metadata["creator"],
        "creator_name": metadata["creator_name"],
        "creator_verified": metadata["creator_verified"],
        "creator_followers": metadata["creator_followers"],
        "timestamp": metadata["timestamp"],
        "duration": metadata["duration"],
        "views": metadata["views"],
        "plays": metadata["plays"],
        "caption": metadata["caption"],
        "category": category,
        "knowledge": extraction
    }