import sys, json
from services.ingest import ingest_service

def main():
    game = sys.argv[1] if len(sys.argv) > 1 else 'pick3'
    force = (sys.argv[2].lower() == 'true') if len(sys.argv) > 2 else True

    def cb(rows_fetched, total_rows):
        print('PROG', rows_fetched, total_rows, flush=True)

    print('START', game, flush=True)
    try:
        res = ingest_service.fetch_and_sync(game, progress_callback=cb, force=force)
        print('RESULT', json.dumps(res, ensure_ascii=False), flush=True)
    except Exception as e:
        print('ERROR', str(e), flush=True)

if __name__ == '__main__':
    main()
