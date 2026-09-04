import sys

from processing.pipeline import process_reel


def main():
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = input("Instagram Reel URL: ")

    result = process_reel(url)

    if result["success"]:
        if result.get("cached"):
            print(f"[OK] Reel '{result.get('brain', {}).get('id')}' was already processed (returned cached Brain Object).")
        else:
            print("Reel processed successfully.")
    else:
        print(f"Reel processing failed: {result.get('error', 'Unknown error')}")


if __name__ == "__main__":
    main()
