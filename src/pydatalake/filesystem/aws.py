import configparser
import json
import os
from pdb import line_prefix
import subprocess
from pathlib import Path
from typing import Any

import pyarrow.fs as pafs
import s3fs
import yaml
from fsspec.implementations import arrow, local
from fsspec.parquet import *


class AwsCredentialsManager:
    def __init__(
        self,
        profile: str = "default",
        credentials: str | Path | dict[str, str] = "~/.aws/credentials",
    ) -> None:
        self._profile = profile

        if isinstance(credentials, str):
            self._filename = Path(credentials).expanduser()
            self.load_credentials()

        elif isinstance(credentials, dict):
            self._filename = Path("~/.aws/credentials").expanduser()
            self._credentials = credentials

        else:
            self._filename = Path("~/.aws/credentials").expanduser()
            self.load_credentials()

    @staticmethod
    def _load_credentials(filename: str | Path, profile: str) -> dict[str, str]:
        config = configparser.ConfigParser()

        if isinstance(filename, (str, Path)):
            config.read(filename)

            if profile in config.sections():
                return dict(config[profile])
            else:
                raise ValueError(f"given profile {profile} not found in {filename}")

        else:
            raise TypeError(
                f"filename must be of type str or Path and not {type(filename)}."
            )

    def load_credentials(self) -> None:
        filename = self._filename or Path("~/.aws/credentials").expanduser()
        self._credentials = self._load_credentials(
            filename=filename, profile=self._profile
        )

    @staticmethod
    def _write_credentials(
        credentials: dict[str, str], filename: str | Path, profile: str
    ) -> None:

        if isinstance(credentials, dict):

            config = configparser.ConfigParser()
            config[profile] = credentials

            if isinstance(filename, str):
                with open(filename, "a") as f:
                    config.write(f)

            else:
                raise TypeError(
                    f"filename must be of type str or Path and not {type(filename)}."
                )

        else:
            raise TypeError(f"credentials must be of type dict not {type(credentials)}")

    def write_credentials(self) -> None:
        filename = self._filename or Path("~/.aws/credentials").expanduser()
        profile = self._profile or "default"

        self._write_credentials(
            credentials=self._credentials, filename=filename, profile=profile
        )

    @staticmethod
    def _export_env(
        profile: str | None = None, credentials: dict | None = None
    ) -> None:

        if profile is not None:
            os.environ["AWS_PROFILE"] = profile

        elif credentials is not None:
            for k in credentials:
                os.environ[k.upper()] = credentials[k]
        else:
            raise ValueError("either profile or credentials must be not None.")

    def export_env(self) -> None:
        self._export_env(profile=self._profile, credentials=self._credentials)

    def swtich_profile(self, profile: str) -> None:
        self._profile = profile
        self.load_credentials()

    def set_profile(self, profile: str) -> None:
        self._export_env(profile=profile)


from typing import Union

fs_type = Union[s3fs.S3FileSystem, pafs.S3FileSystem, pafs.LocalFileSystem, None]


class FileSystem:
    def __init__(
        self,
        type_: str | None = "s3",
        filesystem: fs_type = None,
        credentials: dict | None = None,
        bucket: str | None = None,
    ) -> None:
        self._bucket = bucket
        self._credentials = credentials

        if credentials is not None:
            self.set_env(**credentials)

        if type_ is not None and filesystem is None:
            self._type = type_

            if self._type == "local":
                self._fs = pafs.LocalFileSystem()
                self._filesystem = local.LocalFileSystem(self._fs)

            elif self._type == "s3":
                self._filesystem = s3fs.S3FileSystem(anon=False)
                self._fs = self._filesystem

            else:
                raise ValueError("type_ must be 'local' or 's3'.")

        elif filesystem is not None:

            if type(filesystem) in [pafs.S3FileSystem, s3fs.S3FileSystem]:
                self._type = "s3"
                if isinstance(filesystem, pafs.S3FileSystem):

                    self._filesystem = arrow.ArrowFSWrapper(filesystem)
                    self._fs = filesystem

                elif isinstance(filesystem, s3fs.S3FileSystem):
                    self._filesystem = filesystem
                    self._fs = filesystem

            elif type(filesystem) == pafs.LocalFileSystem:
                self._type = "local"
                self._filesystem = local.LocalFileSystem(filesystem)
                self._fs = filesystem

            elif filesystem is None:
                self._type = "local"
                self._fs = pafs.LocalFileSystem()
                self._filesystem = local.LocalFileSystem(self._fs)

            else:
                raise TypeError(
                    """filesystem must be 's3fs.S3FileSystem', 'pyarrow.fs.S3FileSystem',
                    'pyarrow.fs.LocalFileSystem' or None."""
                )

        else:
            raise ValueError("type_ or filesystem must not be None.")

    @staticmethod
    def _check_for_s5cmd() -> bool:
        res = subprocess.run("which s5cmd", shell=True, capture_output=True)
        return res.returncode == 0

    @property
    def has_s5cmd(self):
        if not hasattr(self, "_has_s5cmd"):
            self._has_s5cmd = self._check_for_s5cmd()

        return self._has_s5cmd

    def _gen_path(self, path: str) -> str:
        if self._bucket is not None:
            if self._bucket not in path:
                return os.path.join(self._bucket, path)
            else:
                return path
        else:
            return path

    def _strip_path(self, path: str) -> str:
        if self._bucket is not None:
            return path.split(self._bucket)[-1]
        else:
            return path

    def _strip_paths(self, paths:list)->list:
        return list(map(self._strip_path, paths))

    def set_env(self, **kwargs):
        for k in kwargs:
            os.environ[k] = kwargs[k]

    def cat(
        self,
        path: str | list,
        recursive: bool = False,
        on_error: str = "raise",
        **kwargs,
    ):
        """Fetch (potentially multiple) paths' contents


        Args:
            path (str | list): URL(s) of file on this filesystems
            recursive (bool, optional): If True, assume the path(s) are directories,
                and get all the contained files. Defaults to False.
            on_error (str, optional): If raise, an underlying exception will be
                raised (converted to KeyError   if the type is in self.missing_exceptions);
                if omit, keys with exception will simply not be included in the output;
                if "return", all keys are included in the output, but the value will be
                bytes or an exception instance. Defaults to "raise".

        Returns:
            dict: dict of {path: contents} if there are multiple paths
                or the path has been otherwise expanded
        """

        return self._filesystem.cat(
            path=self._gen_path(path), recursive=recursive, on_error=on_error, **kwargs
        )

    def cat_file(
        self,
        path: str,
        version_id: str | None = None,
        start: int | None = None,
        end: int | None = None,
        **kwargs,
    ):
        """Get the content of a file

        Args:
            path (str): URL of file on this filesystems
            version_id (str | None, optional): Defaults to None.
            start (int | None, optional): Bytes limits of the read. If negative, backwards
                from end, like usual python slices. Either can be None for start or
                end of file, respectively. Defaults to None.
            end (int | None, optional): see start. Defaults to None.

        Returns:
            str: file content
        """

        return self._filesystem.cat_file(
            path=self._gen_path(path), version_id=version_id, start=start, end=end, **kwargs
        )

    def checksum(self, path: str, refresh: bool = False):
        """Unique value for current version of file

        Args:
            path (str): path of file to get checksum for.
            refresh (bool, optional): if False, look in local cache for file
                details first. Defaults to False.

        Returns:
            str: checksum
        """

        return self._filesystem.checksum(path=self._gen_path(path), refresh=refresh)

    def copy(
        self,
        path1: str,
        path2: str,
        recursive: bool = False,
        on_error: str | None = None,
        **kwargs,
    ):
        """Copy within two locations in the filesystem.

        Args:
            path1 (str): source path.
            path2 (str): destination path.
            recursive (bool, optional): copy recursive. Defaults to False.
            on_error (str | None, optional): If raise, any not-found exceptions
                will be raised; if ignore any not-found exceptions will cause
                the path to be skipped; defaults to raise unless recursive is true,
        """

        self._filesystem.copy(
            self._gen_path(path1), self._gen_path(path2), recursive=recursive, on_error=on_error, **kwargs
        )

    def cp(self, *args, **kwargs):
        self.copy(*args, **kwargs)

    def cp_file(self, path1: str, path2: str, **kwargs):
        """Copy file between locations on S3.


        Args:
            path1 (str): source path.
            path2 (str): destination path.
            preserve_etag (bool | None, optional): Whether to preserve etag while
                copying. If the file is uploaded as a single part, then it will
                be always equalivent to the md5 hash of the file hence etag will
                always be preserved. But if the file is uploaded in multi parts,
                then this option will try to reproduce the same multipart upload
                while copying and preserve  the generated etag. Defaults to None.
        """

        self._filesystem.cp_file(self._gen_path(path1), self._gen_path(path2), **kwargs)

    def delete(
        self, path: str | list, recursive: bool = False, maxdepth: int | None = None
    ):
        """Delete files.

        Args:
            path (str | list): File(s) to delete.
            recursive (bool, optional): If file(s) are directories, recursively
                delete contents and then also remove the directory. Defaults to False.
            maxdepth (int | None, optional): Depth to pass to walk for finding files
                to delete, if recursive. If None, there will be no limit and infinite
                recursion may be possible. Defaults to None.
        """
        self._filesystem.delete(self._gen_path(path), recursive=recursive, maxdepth=maxdepth)

    def du(self, path: str, total: bool = True, maxdepth: int | None = None, **kwargs):
        """Space used by files within a path

        Args:
            path (str):
            total (bool, optional): whether to sum all the file sizes. Defaults to True.
            maxdepth (int | None, optional): maximum number of directory
                levels to descend, None for unlimited. Defaults to None.
            kwargs: passed to ``ls``
        Return:
            Dict of {fn: size} if total=False, or int otherwise, where numbers
                refer to bytes used.
        """
        return self._filesystem.du(path=self._gen_path(path), total=total, maxdepth=maxdepth, **kwargs)

    def disk_usage(self, *args, **kwargs):
        self.du(*args, **kwargs)

    def download(self, *args, **kwargs):
        self.get(*args, **kwargs)

    def exists(self, path: str) -> bool:
        """Returns True, if path exists, else returns False"""
        return self._filesystem.exists(self._gen_path(path))

    def get(self, rpath: str, lpath: str, recursive: bool = False, **kwargs):
        """Copy file(s) to local.

        Copies a specific file or tree of files (if recursive=True). If lpath
        ends with a "/", it will be assumed to be a directory, and target files
        will go within. Can submit a list of paths, which may be glob-patterns
        and will be expanded.

        The get_file method will be called concurrently on a batch of files. The
        batch_size option can configure the amount of futures that can be executed
        at the same time. If it is -1, then all the files will be uploaded concurrently.
        The default can be set for this instance by passing "batch_size" in the
        constructor, or for all instances by setting the "gather_batch_size" key
        in ``fsspec.config.conf``, falling back to 1/8th of the system limit.
        """
        self._filesystem.get(self._gen_path(rpath), lpath, recursive=recursive, **kwargs)

    def get_file(
        self,
        rpath: str,
        lpath: str,
        callback: object | None = None,
    ):
        """Copy single remote file to local"""
        self._filesystem.get_file(self._gen_path(rpath), lpath, callback=callback)

    def glob(self, path:str, **kwargs)->list:
        """Find files by glob-matching.

        If the path ends with '/' and does not contain "*", it is essentially
        the same as ``ls(path)``, returning only files.

        We support ``"**"``,
        ``"?"`` and ``"[..]"``. We do not support ^ for pattern negation.

        Search path names that contain embedded characters special to this
        implementation of glob may not produce expected results;
        e.g., 'foo/bar/*starredfilename*'.

        kwargs are passed to ``ls``."""
        return self._strip_paths(self._filesystem.glob(self._gen_path(path), **kwargs))

    def head(self, path:str, size:int=1024)->str|bytes:
        """Get the first ``size`` bytes from file"""
        path = self._gen_path(path)
        return self._filesystem.head(path=path, size=size)

    def info(self, path, **kwargs)->dict:
        """Give details of entry at path

        Returns a single dictionary, with exactly the same information as ``ls``
        would with ``detail=True``.

        The default implementation should calls ls and could be overridden by a
        shortcut. kwargs are passed on to ```ls()``.

        Some file systems might not be able to measure the file's size, in
        which case, the returned dict will include ``'size': None``.

        Returns
        -------
        dict with keys: name (full path in the FS), size (in bytes), type (file,
        directory, or something else) and other FS-specific keys."""


        return self._filesystem.info(self._gen_path(path), **kwargs)

    def
        

    def invalidate_cache(self, path: str | None = None)->None:
        """Discard any cached directory information

        Parameters
        ----------
        path: string or None
            If None, clear all listings cached else listings at or under given
            path."""
        path = self._gen_path(path)
        self._filesystem.invalidate_cache(path=path)

    def isfile(
        self,
        path: str,
    ) -> bool:
        """Is this entry file-like?"""
        return self._filesystem.isfile(self._gen_path(path))

    def isdir(
        self,
        path: str,
    ) -> bool:
        """Is this entry directory-like?"""
        return self._filesystem.isdir(self._gen_path(path))

    def lexists(self, poath:str) ->bool:
        """If there is a file at the given path (including
        broken links)"""
        return self._filesystem.lexists(self._gen_path(path))

    def listdir(self, path:str, detail:bool=True)->list:
        return self._strip_paths(self._filesystem.listdir(self._gen_path(path), detail=detail))

    def ls(self, path:str, detail:bool=False, **kwargs)->list:
        """List objects at path.

        This should include subdirectories and files at that location. The
        difference between a file and a directory must be clear when details
        are requested.

        The specific keys, or perhaps a FileInfo class, or similar, is TBD,
        but must be consistent across implementations.
        Must include:

        - full path to the entry (without protocol)
        - size of the entry, in bytes. If the value cannot be determined, will
        be ``None``.
        - type of entry, "file", "directory" or other

        Additional information
        may be present, aproriate to the file-system, e.g., generation,
        checksum, etc.

        May use refresh=True|False to allow use of self._ls_from_cache to
        check for a saved listing and avoid calling the backend. This would be
        common where listing may be expensive.

        Parameters
        ----------
        path: str
        detail: bool
            if True, gives a list of dictionaries, where each is the same as
            the result of ``info(path)``. If False, gives a list of paths
            (str).
        kwargs: may have additional backend-specific options, such as version
            information

        Returns
        -------
        List of strings if detail is False, or list of directory information
        dicts if detail is True."""

        return self._strip_paths(self._filesystem.ls(self._gen_path(path), detail=True, **kwargs))
    
    def open(self, path: str, mode="r", **kwargs):
        return self._filesystem.open(path, mode=mode, **kwargs)

    def rm(
        self, path: str | list, recursive: bool = False, maxdepth: int | None = None
    ):
        """Delete files.

        Args:
            path (str | list): File(s) to delete.
            recursive (bool, optional): If file(s) are directories, recursively
                delete contents and then also remove the directory. Defaults to False.
            maxdepth (int | None, optional): Depth to pass to walk for finding files
                to delete, if recursive. If None, there will be no limit and infinite
                recursion may be possible. Defaults to None.
        """
        path = self._gen_path(path)
        self._filesystem.rm(path, recursive=recursive, maxdepth=maxdepth)
