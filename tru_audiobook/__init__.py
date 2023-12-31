import json
import os
import re
import subprocess
import requests
import tempfile
from os.path import expanduser

import audible
import trulogger


class TruAudiobook:

    def __init__(
            self,
            dry_run: bool = False,
            quiet: bool = False,
            verbose: bool = False,
            audible_authfile: str = '~/.config/truaudiobook/audible.json',
            book_data_file: str = '~/Audiobooks/book_data.json',
            destination_dir: str = '~/Audiobooks',
    ):
        self.result = True
        home = expanduser("~")
        self.audible_authfile = audible_authfile.replace('~', home)
        self.dry_run = dry_run
        self.quiet = quiet
        self.verbose = verbose
        self.book_data_file = book_data_file.replace('~', home)
        self.base_destination_dir = destination_dir.replace('~', home)
        self._destination_dir = None

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

    def run(self):
        with open(self.book_data_file) as data_file:
            book_data_list = json.load(data_file)
            for book_data in book_data_list:
                if not book_data.get('active', True):
                    continue
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
        else:
            parts = [_part, 0]
        return parts

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

            _part, _start = self.get_part(_item['path'])
            if last_duration_part is None:
                last_duration_part = _part
            if durations[last_duration_part] != durations[_part]:
                offset += durations[last_duration_part]
                last_duration_part = _part
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

    def process_contents(self, data: dict):
        """
        Process the contents of the audiobook
        :param data: audiobook data
        :return: True if successful, else False
        """

        spine = data['spine']
        buid = data['-odread-buid']
        bonafides = data['-odread-bonafides-d']
        author = self.get_author_from_data(data)
        title_dict = data.get('title')
        title = title_dict.get('main')
        search_title = title_dict.get('search', title)
        clean_title = self.clean_string(title, [("'", "")])
        self.destination_dir = [author, title]
        if os.path.isdir(self.destination_dir):
            self.logger.warning(f"Destination directory already exists: '{self.destination_dir}'")
            return True
        book_data = self.get_book_data_from_audible(author=author, title=search_title)
        try:
            date = book_data['release_date']
            cover_image_url = book_data['product_images']['500']
        except KeyError:
            raise KeyError(f"Could not find date or cover image for '{title}'")

        headers = {}
        # cookie data
        cookies = {
            '_gcl_au': '1.1.319448057.1682810371',
            'od_track': '3',
            '_ga_81HPB4CQ6L': 'GS1.1.1682810392.1.1.1682810435.17.0.0',
            'bifocal%3A_bank-version': '%22b002%22',
            '_sscl_bifocal%3A_bank-version': '%22b002%22',
            'bifocal%3Amigration%3Ab001': '{%22del%22:{}%2C%22add%22:{}%2C%22exp%22:1685402669394}',
            '_sscl_bifocal%3Amigration%3Ab001': '{%22del%22:{}%2C%22add%22:{}%2C%22exp%22:1685402669394}',
            'bifocal%3Adevice-id': '%22c788797d-0829-4483-9ae5-ccaaa77b2091%22',
            '_sscl_bifocal%3Adevice-id': '%22c788797d-0829-4483-9ae5-ccaaa77b2091%22',
            'bifocal%3Aaudiobook%3Apbr': '1',
            '_sscl_bifocal%3Aaudiobook%3Apbr': '1',
            '_gid': 'GA1.2.687037636.1684178026',
            'd': bonafides,
            '_sscl_d': bonafides,
            '_ga': 'GA1.1.1381083922.1682810371',
            '_ga_K0KB8V5TMY': 'GS1.1.1684550382.66.1.1684550504.57.0.0',
        }
        with tempfile.TemporaryDirectory(
                prefix="tru_audiobook", suffix=clean_title.replace(" ", ""), dir=f"{expanduser('~')}/Downloads"
        ) as tmp_download:
            self.logger.info(f'Created temporary directory {tmp_download}')
            if not os.path.isdir(tmp_download):
                os.mkdir(tmp_download)

            final_file = f"{tmp_download}/{clean_title}.mp3"

            # this will return a tuple of root and extension
            file_parts = os.path.splitext(cover_image_url)
            ext = file_parts[1]
            cover_image_ext = ext if ext in ["jpg", "png"] else "jpg"
            cover_image_file = f"{tmp_download}/cover.{cover_image_ext}"

            with open(cover_image_file, 'wb') as file_handle:
                file_handle.write(requests.get(cover_image_url).content)

            all_found = True
            input_files = []
            durations = {}
            for index, item in enumerate(spine):
                part, _ = self.get_part(item['-odread-original-path'])
                file_path = f"{tmp_download}/{part.lower()}"
                input_files.append(f"file '{file_path}'")
                durations[part] = float(item['audio-duration'])
                url = f"https://ofs-{buid}.listen.overdrive.com/{item['path'].replace('%3D', '=')}"
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

            if not all_found:
                self.logger.error("Some parts are missing (see above) - download the missing parts and try again")
                return False

            file_list_file = f"{tmp_download}/files.txt"
            with open(file_list_file, "w") as file_handle:
                file_handle.write("\n".join(input_files))

            if not os.path.isfile(final_file):
                # merge the video files
                cmd = [
                    "ffmpeg", "-f", "concat", "-safe", "0", "-loglevel", "quiet", "-i", file_list_file, "-c", "copy", final_file
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
