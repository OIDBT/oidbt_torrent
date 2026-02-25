import enum
import hashlib
import itertools
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

from bencode2 import bdecode, bencode
from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from _hashlib import HASH


class Torrent:
    class Parse_error(Exception):
        pass

    class Data(BaseModel):
        class Info(BaseModel):
            type File = dict[bytes, bytes | int | list[bytes]]
            type File_tree = dict[bytes, File_tree | File]
            model_config = ConfigDict(extra="allow", frozen=True)

            name: bytes
            piece_length: int = Field(alias="piece length")
            source: bytes | None = Field(alias="source", default=None)

            length: int | None = Field(default=None)  # v1 单文件
            files: list[File] | None = Field(default=None)  # v1 多文件
            pieces: bytes | None = Field(default=None)  # v1

            file_tree: File_tree | None = Field(alias="file tree", default=None)  # v2
            meta_version: int | None = Field(alias="meta version", default=None)  # v2

        model_config = ConfigDict(extra="allow", frozen=True)

        announce: bytes | None = Field(default=None)
        announce_list: list[list[bytes]] | None = Field(
            alias="announce-list", default=None
        )
        comment: bytes | None = Field(default=None)
        created_by: bytes | None = Field(alias="created by", default=None)
        creation_date: int | None = Field(alias="creation date", default=None)
        info: Info
        url_list: list[bytes] | bytes | None = Field(alias="url-list", default=None)

        piece_layers: dict[bytes, bytes] | None = Field(
            alias="piece layers", default=None
        )  # v2

    def get_str_key_data_dict(
        self,
        data_dict: dict[str, Any]
        | dict[bytes, Any]
        | dict[str | bytes, Any]
        | None = None,
    ) -> dict[str, Any]:
        """只能保证被 Pydantic 解析，不转换所有 key"""
        if data_dict is None:
            data_dict = self._data_dict
        return {
            (
                k.decode()
                if isinstance(k, bytes)
                else k
                if isinstance(k, str)
                else str(k)
            ): (
                self.get_str_key_data_dict(v)
                if isinstance(v, dict) and k == b"info"
                else v
            )
            for k, v in data_dict.items()
        }

    def _refresh_data(self) -> None:
        try:
            self._data: Torrent.Data = self.Data(**self.get_str_key_data_dict())
        except ValidationError as e:
            raise self.Parse_error("Pydantic 检测不通过") from e

        self._data_bytes = bencode(self._data_dict)

    class Torrent_format(enum.Enum):
        v1 = enum.auto()
        v2 = enum.auto()
        hybrid = enum.auto()

    def get_torrent_format(self) -> Torrent.Torrent_format:
        match self._data.info.meta_version:
            case None:
                return self.Torrent_format.v1
            case 2:
                return (
                    self.Torrent_format.v2
                    if self._data.info.files is None
                    else self.Torrent_format.hybrid
                )
            case _:
                raise ValueError("未知的 torrent format")

    def get_hash_v1(self) -> HASH:
        return hashlib.sha1(bencode(self._data_dict[b"info"]))

    def get_hash_v2(self) -> HASH:
        return hashlib.sha256(bencode(self._data_dict[b"info"]))

    @dataclass(slots=True, kw_only=True)
    class Info:
        format: Torrent.Torrent_format
        hash_v1: HASH | None = None
        hash_v2: HASH | None = None

    def _refresh_info(self) -> None:
        self.info = self.Info(
            format=(_format := self.get_torrent_format()),
        )
        match _format:
            case self.Torrent_format.v1:
                self.info.hash_v1 = self.get_hash_v1()
            case self.Torrent_format.v2:
                self.info.hash_v2 = self.get_hash_v2()
            case self.Torrent_format.hybrid:
                self.info.hash_v1 = self.get_hash_v1()
                self.info.hash_v2 = self.get_hash_v2()

    def _refresh(self) -> None:
        self._refresh_data()
        self._refresh_info()

    @property
    def data_bytes(self) -> bytes:
        return self._data_bytes

    @property
    def data_dict(self):
        return self._data_dict

    @data_dict.setter
    def data_dict(self, value) -> None:
        """修改这个属性后运行刷新函数，即可刷新其他 data 属性"""
        self._data_dict = value
        self._refresh()

    @property
    def data(self) -> Data:
        return self._data

    def __init__(self, file: Path | bytes) -> None:
        self._data_bytes = file.read_bytes() if isinstance(file, Path) else file

        self._data_dict: dict[bytes, Any] = bdecode(self._data_bytes)
        if not isinstance(self._data_dict, dict):
            raise self.Parse_error("结构不是 dict")
        if not all(isinstance(k, bytes) for k in self._data_dict):
            raise self.Parse_error("第一层有 key 不是 bytes")

        self._refresh()

        _e = self.Parse_error("文件格式错误")
        match self.info.format:
            case self.Torrent_format.v1:
                if self.data.info.pieces is None or (
                    self.data.info.files is None and self.data.info.length is None
                ):
                    raise _e
            case self.Torrent_format.v2:
                if (
                    self.data.info.file_tree is None
                    or self.data.info.meta_version is None
                    or self.data.piece_layers is None
                ):
                    raise _e
            case self.Torrent_format.hybrid:
                if (
                    self.data.info.pieces is None
                    or (self.data.info.files is None and self.data.info.length is None)
                    or self.data.info.file_tree is None
                    or self.data.info.meta_version is None
                    or self.data.piece_layers is None
                ):
                    raise _e
        if self.data.info.files is not None and self.data.info.length is not None:
            raise _e

    @overload
    def _get_file_tree_xl(self, point: Data.Info.File_tree | Data.Info.File) -> int: ...
    @overload
    def _get_file_tree_xl(
        self, point: Data.Info.File_tree | Data.Info.File | None = None
    ) -> int | None: ...
    def _get_file_tree_xl(
        self, point: Data.Info.File_tree | Data.Info.File | None = None
    ) -> int | None:
        if point is None:
            point = self.data.info.file_tree
            if point is None:
                return None

        xl: int = 0
        for k, v in point.items():
            if k == b"length" and isinstance(v, int):
                xl += v
            elif isinstance(v, dict):
                xl += self._get_file_tree_xl(v)
        return xl

    def _get_files_xl(self) -> int | None:
        if self.data.info.files is not None:
            return sum(self._get_file_tree_xl(file) for file in self.data.info.files)
        return self.data.info.length

    def get_xl(self) -> int:
        xl = (
            self._get_file_tree_xl() or self._get_files_xl()
        )  # qBit 的混合种子的 xl 是 v1 的 xl，这里与 qBit 不一样，使用的是 v2 的 xl
        assert xl is not None
        return xl

    def get_magnet(
        self,
        *,
        dn: bool = True,
        xl: bool = True,
        ws: bool = True,
        tr: bool = True,
        only_one_tr: bool = False,
    ) -> str:
        """
        动态生成磁链

        :param dn: Display name
        :param xl: eXact Length
        :param ws: Web Seed
        :param tr: Traker
        :param only_one_tr: 只保留一个 traker, 仅在 tr=True 时生效
        """
        magnet_pieces: list[str] = []

        match self.info.format:
            case self.Torrent_format.v1:
                assert self.info.hash_v1 is not None
                magnet_pieces.append("xt=urn:btih:" + self.info.hash_v1.hexdigest())
            case self.Torrent_format.v2:
                assert self.info.hash_v2 is not None
                magnet_pieces.append("xt=urn:btmh:1220" + self.info.hash_v2.hexdigest())
            case self.Torrent_format.hybrid:
                assert self.info.hash_v1 is not None and self.info.hash_v2 is not None
                magnet_pieces.append("xt=urn:btih:" + self.info.hash_v1.hexdigest())
                magnet_pieces.append("xt=urn:btmh:1220" + self.info.hash_v2.hexdigest())

        if dn:
            magnet_pieces.append("dn=" + urllib.parse.quote(self.data.info.name))

        if xl:
            magnet_pieces.append(f"xl={self.get_xl()}")

        if ws and self.data.url_list is not None:
            for url in (
                self.data.url_list
                if isinstance(self.data.url_list, list)
                else [self.data.url_list]
            ):
                magnet_pieces.append("ws=" + urllib.parse.quote(url))

        if tr:
            announce_list: list[list[bytes]] = self.data.announce_list or (
                [] if self.data.announce is None else [[self.data.announce]]
            )
            if only_one_tr:
                announce_list = announce_list[:1]
            for url in itertools.chain.from_iterable(announce_list):
                magnet_pieces.append("tr=" + urllib.parse.quote(url))

        return "magnet:?" + "&".join(magnet_pieces)
