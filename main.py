import sys

from processing.pipeline import process_reel


def main():
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = input("Instagram Reel URL: ")

    result = process_reel(url)

    if result["success"]:
        print("Reel processed successfully.")
    else:
        print("Reel processing failed.")


if __name__ == "__main__":
    main()
