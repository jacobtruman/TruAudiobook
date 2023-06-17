#!/usr/bin/env python

import os
import sys
import glob
import argparse

from tru_audiobook import TruAudiobook


def list_str(values):
    return values.split(',')


def parse_args():
    """
    Parse command line arguments.
    """
    parser = argparse.ArgumentParser(
        description='Run TruAudiobook Functions',
    )

    parser.add_argument(
        '-d', '--dry_run',
        action='store_true',
        dest='dry_run',
        help='Dry run mode',
        default=False,
    )

    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        dest='quiet',
        help='All prompts are skipped and continue with defaults',
        default=False,
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        dest='verbose',
        help='Enable verbose logging',
    )

    parser.add_argument(
        '-a', '--audible_authfile',
        dest='audible_authfile',
        help="Audible authfile",
        default=os.environ.get('AUDIBLE_AUTHFILE', "~/.config/truaudiobook/audible.json"),
    )

    parser.add_argument(
        '-b', '--book_data_file',
        dest='book_data_file',
        help="Book data file",
        default=os.environ.get('BOOK_DATA_FILE', "~/Audiobooks/book_data.json"),
    )

    parser.add_argument(
        '-m', '--destination_dir',
        dest='destination_dir',
        help="Media destination directory",
        default=os.environ.get('DESTINATION_DIR', '~/Audiobooks'),
    )

    args = parser.parse_args()

    if False:
        parser.error("Some error")

    return args


def main():
    args = parse_args()

    truaudiobook = TruAudiobook(
        dry_run=args.dry_run,
        quiet=args.quiet,
        verbose=args.verbose,
        audible_authfile=args.audible_authfile,
        book_data_file=args.book_data_file,
        destination_dir=args.destination_dir,
    )

    result = truaudiobook.run()

    if not result:
        sys.exit(1)


if __name__ == '__main__':
    main()
