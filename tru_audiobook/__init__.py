import base64
import json
import os
import re
import subprocess
import requests
import tempfile
import urllib.parse
from glob import glob
from os.path import expanduser

import ffmpeg
import audible
import trulogger

DEFAULT_AUDIBLE_AUTHFILE = '~/.config/truaudiobook/audible.json'
DEFAULT_DESTINATION_DIR = '~/Audiobooks'
DEFAULT_BOOK_DATA_DIR = f'~/{DEFAULT_DESTINATION_DIR}/book_data'


class TruAudiobook:

    def __init__(
            self,
            dry_run: bool = False,
            quiet: bool = False,
            verbose: bool = False,
            dev: bool = False,
            audible_authfile: str = DEFAULT_AUDIBLE_AUTHFILE,
            book_data_dir: str = DEFAULT_BOOK_DATA_DIR,
            destination_dir: str = DEFAULT_DESTINATION_DIR,
    ):
        self.result = True
        self.audible_authfile = self.resolve_path(audible_authfile)
        self.dry_run = dry_run
        self.quiet = quiet
        self.verbose = verbose
        self.dev = dev
        self.book_data_dir = self.resolve_path(book_data_dir)
        self.base_destination_dir = self.resolve_path(destination_dir)
        self._destination_dir = None
        self.book_data = []

        self.logger = trulogger.TruLogger({'verbose': verbose})
        self._set_log_prefix()

    @property
    def destination_dir(self):
        return self._destination_dir

    @destination_dir.setter
    def destination_dir(self, values):
        self._destination_dir = f"{self.base_destination_dir}/{'/'.join(values)}"
        self._set_log_prefix(values)

    def _set_log_prefix(self, prefix=None):
        _prefix = ""
        if self.dry_run:
            _prefix += "[ DRY RUN ] "
        if prefix is not None:
            if isinstance(prefix, list):
                for item in prefix:
                    _prefix += f"[ {item} ] "
            elif isinstance(prefix, str):
                _prefix += f"[ {prefix} ] "
        self.logger.set_prefix(_prefix)

    @staticmethod
    def resolve_path(path):
        return os.path.abspath(os.path.expanduser(path))

    @staticmethod
    def _get_duration_ffmpeg(file_path):
        """
        Get duration of audio file
        :param file_path: Path to audio file
        :return: duration of audio file in seconds (float)
        """
        probe = ffmpeg.probe(file_path)
        stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'audio'), None)
        return float(stream['duration'])

    @staticmethod
    def _valid_book_data(book_data):
        if isinstance(book_data, dict):
            return book_data.get('active', True)
        return False

    def _add_valid_book_data(self, book_data):
        if self._valid_book_data(book_data):
            self.book_data.append(book_data)

    def _collect_book_datas(self):
        book_data_files = glob(f"{self.book_data_dir}/*.json")
        for book_data_file in book_data_files:
            with open(book_data_file) as data_file:
                try:
                    book_data = json.load(data_file)
                    if isinstance(book_data, list):
                        for _book_data in book_data:
                            self._add_valid_book_data(_book_data)
                    else:
                        self._add_valid_book_data(book_data)
                except json.JSONDecodeError as jerr:
                    self.logger.error(f"Error loading JSON data from file {book_data_file}: {jerr}")

    def run(self):
        self._collect_book_datas()
        if len(self.book_data) == 0:
            self.logger.error(f"No active book data found in directory: {self.book_data_dir}")
            return False
        for index, book_data in enumerate(self.book_data, 1):
            self.logger.info(f"Processing book {index} / {len(self.book_data)}")
            self.result = self.process_contents(data=book_data)
        return self.result

    def get_audible_client(self) -> audible.Client:
        """
        Get client for interacting with audible
        :return: audible client
        """
        auth = audible.Authenticator.from_file(self.audible_authfile)
        return audible.Client(auth)

    def get_book_data_from_audible(self, author: str, title: str) -> dict:
        """
        Get book data from audible
        :param author: author
        :param title: title
        :return: book data
        """
        with self.get_audible_client() as client:
            books = client.get(
                "1.0/catalog/products",
                num_results=10,
                response_groups="product_desc, product_attrs, media",
                author=author,
                title=title,
            )
            if len(books) == 0:
                raise Exception(f"Book not found: {author} - {title}")

            book = {}
            for _book in books['products']:
                if _book['title'].startswith(title):
                    book = _book
                    break
            return book

    def convert_chapters(self, chapters: dict, source_file: str, cover_image_file: str, _encode: bool = False):
        """
        Convert chapters to individual files
        :param chapters: list of chapters
        :param source_file: source file
        :param cover_image_file: path to cover image file
        :param _encode: encode (this takes longer)
        """
        chapter_count = len(chapters)
        self.logger.info(f"Processing {chapter_count} chapters")

        os.makedirs(self.destination_dir, exist_ok=True)

        index = 0
        for title, chapter in chapters.items():
            index += 1
            outfile = f"{self.destination_dir}/{chapter['outfile']}"
            if os.path.isfile(outfile):
                self.logger.warning(f"File already exists: '{outfile}'")
                continue
            self.logger.info(f"Processing ({index}/{chapter_count}): {chapter['title']}")
            command = [
                "ffmpeg", '-ss', f"{chapter['start']}", '-to', f"{chapter['end']}",
                '-i', source_file, '-i', cover_image_file, '-map', '0:a', '-map', '1:0',
            ]

            for attribute in ["title", "track", "artist", "album_artist", "album", "genre", "date"]:
                if attribute in chapter:
                    command += ["-metadata", f"{attribute}={chapter[attribute]}"]
            if _encode:
                command += ['-c:v', 'libx264']
            else:
                command += ['-c', 'copy']
            command += [outfile]

            try:
                # ffmpeg requires an output file and so it errors when it does not get one
                if not self.dry_run:
                    subprocess.check_output(command, stderr=subprocess.STDOUT, universal_newlines=True)
                else:
                    self.logger.debug(f"Would have run: {command}")
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"command '{e.cmd}' return with error (code {e.returncode}): {e.output}")

    @staticmethod
    def get_part(path):
        """
        Get part from path
        :param path:
        :return:
        """
        _part = path[path.rfind('Part'):]
        if "#" in _part:
            parts = _part.split("#")
        elif "?" in _part:
            parts = _part.split("?")
        else:
            parts = [_part, 0]
        return parts

    @staticmethod
    def get_start(timestamp):
        """
        Get start from timestamp
        :param timestamp:
        :return:
        """
        timestamp_split = list(map(int, timestamp.split(":")))
        if len(timestamp_split) == 2:
            _hour = 0
            _min, _sec = timestamp_split
        elif len(timestamp_split) == 3:
            _hour, _min, _sec = timestamp_split
        else:
            raise RuntimeError(f"Invalid timestamp format ({timestamp}); Should be in format: mm:ss or hh:mm:ss")

        return _hour * 3600 + _min * 60 + _sec

    @staticmethod
    def clean_string(string: str, replaces: list = None):
        """
        Clean string
        :param string: string to clean
        :param replaces: replacements
        :return: cleaned string
        """
        _replaces = [('/', ':'), ("'", "\'")]
        if replaces is not None:
            _replaces += replaces
        for char, repl in _replaces:
            string = string.replace(char, repl)

        match = re.search(r"^\d+\.", string)
        if match is not None:
            string = string.replace(match.group(), '').strip()
        return string

    @staticmethod
    def get_author_from_data(data: dict) -> str:
        """
        Get author from data
        :param data: dict of book data
        :return: author
        """
        author = None
        for item in data['creator']:
            if item['role'] in ["author", "aut"]:
                author = item['name']
        return author

    def compile_chapters(self, author: str, title: str, date: str, toc: dict, durations: dict):
        """
        Compile chapters
        :param author:
        :param title:
        :param date:
        :param toc:
        :param durations:
        :return:
        """

        self.logger.info("Compiling chapters")

        chapters = {}
        last_chapter = None
        last_duration_part = None
        offset = 0
        track_num = 0
        for _item in toc:
            chapter_title = self.clean_string(_item['title'])
            if chapter_title in chapters:
                continue
            track_num += 1

            if "timestamp" in _item:
                _start = self.get_start(_item['timestamp'])
            elif "path" in _item:
                _part, _start = self.get_part(_item['path'])
                if last_duration_part is None:
                    last_duration_part = _part
                if durations[last_duration_part] != durations[_part]:
                    offset += durations[last_duration_part]
                    last_duration_part = _part
            else:
                raise Exception(f"Unknown chapter reference: {_item}")
            _start = float("{:.4f}".format(float(_start) + offset))
            chapters[chapter_title] = {
                "title": chapter_title,
                "track": str(track_num),
                "artist": author,
                "album_artist": author,
                "album": title,
                "genre": "Audiobook",
                "date": date,
                "outfile": f"{str(track_num).zfill(2)} - {chapter_title}.mp3",
                "start": _start
            }
            if last_chapter is not None:
                chapters[last_chapter]["end"] = chapters[chapter_title]["start"]
            last_chapter = chapter_title

        # get the last chapter's end time; the full duration
        chapters[last_chapter]['end'] = sum(durations.values())

        # have to add track totals after the fact
        for _, chapter in chapters.items():
            chapter["track"] += f"/{len(chapters)}"

        return chapters

    def download_and_process(
            self,
            data: dict,
            download_dir: str,
            clean_title: str,
            author: str,
            date: str
    ) -> bool:
        """
        Download and process into specified directory
        :param data: audiobook data
        :param download_dir: Directory in which to download
        :param clean_title: Clean title
        :param author: Author name
        :param date: Release date of audiobook
        :return: True if successful, else False
        """
        spine = data['spine']
        crid = data["-odread-crid"][0].upper()
        buid = data['-odread-buid']
        bonafides = urllib.parse.quote(data['-odread-bonafides-d'])

        headers = {}
        # cookie data
        cookies = {
            '_ga': 'GA1.1.542180755.1714618874',
            'bifocal%3A_bank-version': '%22b002%22',
            '_sscl_bifocal%3A_bank-version': '%22b002%22',
            'bifocal%3Amigration%3Ab001': '{%22del%22:{}%2C%22add%22:{}%2C%22exp%22:1717278499669}',
            '_sscl_bifocal%3Amigration%3Ab001': '{%22del%22:{}%2C%22add%22:{}%2C%22exp%22:1717278499669}',
            'bifocal%3Adevice-id': '%22ca04a0d4-1580-4247-8930-2efd7ae04be8%22',
            '_sscl_bifocal%3Adevice-id': '%22ca04a0d4-1580-4247-8930-2efd7ae04be8%22',
            'bifocal%3Aaudiobook%3Apbr': '1',
            '_sscl_bifocal%3Aaudiobook%3Apbr': '1',
            '_ga_WKH7FNBY8W': 'GS1.1.1715373567.5.1.1715373642.60.0.0',
            '_ga_JFPV2QK2H1': 'GS1.1.1715373567.5.1.1715373642.0.0.1700230639',
            'd': bonafides,
            '_sscl_d': bonafides,
        }

        if not os.path.isdir(download_dir):
            self.logger.info(f'Created {"download" if self.dev else "temporary"} directory {download_dir}')
            os.mkdir(download_dir)

        self.logger.info(f'Downloading to directory {download_dir}')

        final_file = f"{download_dir}/{clean_title}.mp3"

        cover_image_url = self._get_cover_image_url(data)
        # this will return a tuple of root and extension
        file_parts = os.path.splitext(cover_image_url)
        ext = file_parts[1]
        cover_image_ext = ext if ext in ["jpg", "png"] else "jpg"
        cover_image_file = f"{download_dir}/cover.{cover_image_ext}"

        with open(cover_image_file, 'wb') as file_handle:
            file_handle.write(requests.get(cover_image_url).content)

        all_found = True
        input_files = []
        durations = {}
        for index, item in enumerate(spine):
            part = f"Part{str((index + 1)).zfill(2)}"
            file_path = f"{download_dir}/{part}.mp3"
            input_files.append(f"file '{file_path}'")
            if data.get('overdrive', False):
                prefix = "ofs"
                domain = "listen.overdrive.com"
            else:
                prefix = "dewey"
                domain = "listen.libbyapp.com"

            if "id" in item:
                spine_index = base64.b64encode("{{\"spine\":{index}}}".format(index=index).encode()).decode()
                url_path = f"{{{crid}}}Fmt425-{part}.mp3?cmpt={spine_index}--{item['id']}"
            elif "path" in item:
                url_path = item['path']
            else:
                self.logger.error(f"Unknown spine item: {item}")
                continue
            url = f"https://{prefix}-{buid}.{domain}/{url_path}"
            if not os.path.isfile(file_path):
                self.logger.warning(f"Part NOT found: {file_path}")

                response = requests.get(
                    url,
                    cookies=cookies,
                    headers=headers,
                )

                if response.status_code == 200:
                    with open(file_path, 'wb') as file_handle:
                        file_handle.write(response.content)
                    self.logger.info(f"Downloaded part: {file_path}")
                else:
                    all_found = False
                    self.logger.error(f"Unable to download part: {url}")

            # don't want to rely on this being available or accurate; get it directly from the file
            # durations[part] = float(item['audio-duration'])
            durations[part] = self._get_duration_ffmpeg(file_path)

        if not all_found:
            self.logger.error("Some parts are missing (see above) - download the missing parts and try again")
            return False

        file_list_file = f"{download_dir}/files.txt"
        with open(file_list_file, "w") as file_handle:
            file_handle.write("\n".join(input_files))

        if not os.path.isfile(final_file):
            # merge the video files
            cmd = [
                "ffmpeg", "-f", "concat", "-safe", "0", "-loglevel", "quiet", "-i", file_list_file, "-c", "copy",
                final_file
            ]

            self.logger.info(f"Merging parts into '{final_file}'...")
            if not self.dry_run:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE)

                fout = p.stdin
                fout.close()
                p.wait()

                if p.returncode != 0:
                    raise subprocess.CalledProcessError(p.returncode, cmd)

            self.logger.info(f"Merge complete")

        self.convert_chapters(
            chapters=self.compile_chapters(
                author=author,
                title=clean_title,
                date=date,
                toc=data['nav']['toc'],
                durations=durations,
            ),
            source_file=final_file,
            cover_image_file=cover_image_file,
        )
        return True

    def _get_cover_image_url(self, data):
        crid = data["-odread-crid"][0].upper()
        crid0 = crid.split("-")[0]
        crid1, crid2, crid3 = crid0[0:3], crid0[3:6], crid0[6:8]
        url = 'https://libbyapp.com/covers/resize'
        params = {
            "type": "auto",
            "width": 536,
            "height": 536,
            "quality": 80,
            "force": True,
            "url": f"%2FImageType-400%2F0293-1%2F{crid1}%2F{crid2}%2F{crid3}%2F%257B{crid}%257DImg400.jpg"
        }
        return f"{url}?{urllib.parse.urlencode(params)}"

    def process_contents(self, data: dict):
        """
        Process the contents of the audiobook
        :param data: audiobook data
        :return: True if successful, else False
        """

        author = self.get_author_from_data(data)
        title_dict = data.get('title')
        title = title_dict.get('main')
        search_title = title_dict.get('search', title)
        clean_title = self.clean_string(title, [("'", "")])
        self.destination_dir = [author, title]
        if not self.dev and os.path.isdir(self.destination_dir):
            self.logger.warning(f"Destination directory already exists: '{self.destination_dir}'")
            return True
        book_data = self.get_book_data_from_audible(author=author, title=search_title)
        try:
            date = book_data['release_date']
        except KeyError:
            raise KeyError(f"Could not find date for '{title}'")

        if not self.dev:
            with tempfile.TemporaryDirectory(
                prefix="tru_audiobook",
                suffix=clean_title.replace(" ", ""),
                dir=self.resolve_path("~/Downloads"),
            ) as tmp_download:
                return self.download_and_process(data, tmp_download, clean_title, author, date)
        else:
            tmp_download = self.resolve_path(f"~/Downloads/tru_audiobook_{clean_title.replace(' ', '')}")
            return self.download_and_process(data, tmp_download, clean_title, author, date)
