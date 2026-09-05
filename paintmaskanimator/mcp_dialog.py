"""「MCP サーバー設定…」ダイアログ。

MCP サーバーが動くことと、ユーザーが導入できることは別の問題である。設定ファイルの
場所を調べ、絶対パスを書き、Windows のバックスラッシュを二重にし、再起動する――
この手順を作画のユーザーに求めるのは現実的ではない。ここはその全部をボタン2つに
畳む。

登録先（Claude Desktop / Codex CLI）は形式も場所も違うが、その差は
:class:`paintmaskanimator.mcp.setup.ClientTarget` が吸収するので、この画面は
選ばれた登録先をそのまま呼ぶだけでよい。

判定と書き込みの中身は :mod:`paintmaskanimator.mcp.setup` にあり、このファイルは
その表示と操作だけを持つ。``mcp`` パッケージが未導入でも開ける（むしろ、未導入だと
いうことを伝えるために開ける必要がある）。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .i18n import tr
from .logging_setup import get_logger
from .mcp import setup as mcp_setup

log = get_logger(__name__)


class McpSetupDialog(QDialog):
    """導入状況の確認と、Claude への登録をまとめて行うダイアログ。"""

    def __init__(self, parent=None, *, project_path: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle(tr("MCP サーバー設定"))
        self.setMinimumWidth(620)
        self._project_path = project_path

        layout = QVBoxLayout(self)

        intro = QLabel(
            tr(
                "Claude Desktop・Claude Code・Codex CLI から、このソフトの"
                "プロジェクトを読ませるための設定です。<b>アプリに AI は入りません。</b>"
                "AI の実行はお使いのクライアント側で行われるため、API キーの入力は不要です。"
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # --- 診断 -------------------------------------------------------
        status_box = QGroupBox(tr("導入状況"))
        status_layout = QVBoxLayout(status_box)
        self.status_view = QPlainTextEdit()
        self.status_view.setReadOnly(True)
        self.status_view.setMaximumHeight(200)
        status_layout.addWidget(self.status_view)
        recheck = QPushButton(tr("再確認"))
        recheck.clicked.connect(self.refresh)
        status_row = QHBoxLayout()
        status_row.addStretch(1)
        status_row.addWidget(recheck)
        status_layout.addLayout(status_row)
        layout.addWidget(status_box)

        # --- 選択肢 -----------------------------------------------------
        options_box = QGroupBox(tr("設定内容"))
        options_layout = QVBoxLayout(options_box)

        client_row = QHBoxLayout()
        client_row.addWidget(QLabel(tr("登録先:")))
        self.client_selector = QComboBox()
        for key, target in mcp_setup.CLIENTS.items():
            self.client_selector.addItem(target.label, key)
        self.client_selector.setCurrentIndex(
            max(0, self.client_selector.findData(mcp_setup.DEFAULT_CLIENT))
        )
        self.client_selector.currentIndexChanged.connect(self._client_changed)
        client_row.addWidget(self.client_selector, 1)
        options_layout.addLayout(client_row)

        self.include_project = QCheckBox(
            tr("起動時に、いま開いているプロジェクトを渡す")
        )
        self.include_project.setChecked(bool(project_path))
        self.include_project.setEnabled(bool(project_path))
        if not project_path:
            self.include_project.setToolTip(
                tr("プロジェクトを保存してから設定すると、この項目を選べます。")
            )
        self.allow_write = QCheckBox(tr("AI による塗りと保存を許可する"))
        self.allow_write.setToolTip(
            tr(
                "OFF のあいだ、AI はプロジェクトを読むだけで書き換えできません。"
                "ON にしても、保存は既定で別名保存になり元のファイルは上書きされません。"
            )
        )
        options_layout.addWidget(self.include_project)
        options_layout.addWidget(self.allow_write)
        for widget in (self.include_project, self.allow_write):
            widget.toggled.connect(self.refresh_config_text)
        layout.addWidget(options_box)

        # --- 設定内容 ---------------------------------------------------
        self.config_box = QGroupBox(tr("設定（自動生成）"))
        config_box = self.config_box
        config_layout = QVBoxLayout(config_box)
        self.config_view = QPlainTextEdit()
        self.config_view.setReadOnly(True)
        self.config_view.setMaximumHeight(170)
        config_layout.addWidget(self.config_view)

        # 主操作（登録）と、手作業に落ちる人向けの操作を段で分ける。1行に4つ
        # 並べるとボタン名が切れて「何を押せばいいか」が読めなくなる。
        self.install_button = QPushButton()
        self.install_button.setDefault(True)
        self.install_button.clicked.connect(self.install)
        open_folder = QPushButton(tr("設定フォルダを開く"))
        open_folder.clicked.connect(self.open_config_folder)
        primary_row = QHBoxLayout()
        primary_row.addWidget(self.install_button, 2)
        primary_row.addWidget(open_folder, 1)
        config_layout.addLayout(primary_row)

        copy_json = QPushButton(tr("設定をコピー"))
        copy_json.clicked.connect(self.copy_config)
        self.copy_command_button = QPushButton()
        self.copy_command_button.clicked.connect(self.copy_command)
        copy_command = self.copy_command_button
        copy_row = QHBoxLayout()
        copy_row.addWidget(copy_json, 1)
        copy_row.addWidget(copy_command, 1)
        config_layout.addLayout(copy_row)
        layout.addWidget(config_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.refresh()

    # ------------------------------------------------------------------

    def target(self) -> mcp_setup.ClientTarget:
        """いま選ばれている登録先。"""
        return mcp_setup.client_target(self.client_selector.currentData())

    def _client_changed(self):
        self._sync_client_labels()
        self.refresh_config_text()
        self.refresh_install_state()

    def _sync_client_labels(self):
        target = self.target()
        self.install_button.setText(
            tr("{client} に登録").format(client=target.label)
        )
        self.copy_command_button.setText(
            tr("{client} 用コマンドをコピー").format(client=target.cli_label)
        )
        self.config_box.setTitle(
            tr("設定（自動生成・{format}）").format(format=target.format_name)
        )

    def _project_argument(self) -> Optional[str]:
        if self.include_project.isChecked():
            return self._project_path
        return None

    def config_text(self) -> str:
        """登録先の記法（JSON / TOML）で書いた設定。"""
        return self.target().render(
            self._project_argument(), allow_write=self.allow_write.isChecked()
        )

    def refresh(self):
        """診断を走らせ直す。実際に別プロセスを起動するので少し待たせる。"""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            diagnosis = mcp_setup.diagnose()
        finally:
            QApplication.restoreOverrideCursor()
        self.status_view.setPlainText(diagnosis.as_text())
        self._environment_ready = diagnosis.ok
        self._sync_client_labels()
        self.refresh_install_state()
        self.refresh_config_text()

    def refresh_install_state(self):
        # 判定が駄目でも設定の表示とコピーは残す。手で直したい人の助けになる。
        self.install_button.setEnabled(getattr(self, "_environment_ready", False))

    def refresh_config_text(self):
        self.config_view.setPlainText(self.config_text())

    # ------------------------------------------------------------------

    def copy_config(self):
        target = self.target()
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.config_view.toPlainText())
        QMessageBox.information(
            self,
            tr("MCP サーバー設定"),
            tr(
                "設定をコピーしました。\n\n{path}\n\nこのファイルへ貼り付けて、"
                "{client} を再起動してください。"
            ).format(path=target.config_path(), client=target.label),
        )

    def copy_command(self):
        target = self.target()
        command = target.cli_command(
            self._project_argument(), allow_write=self.allow_write.isChecked()
        )
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(command)
        QMessageBox.information(
            self,
            tr("MCP サーバー設定"),
            tr("コマンドをコピーしました。ターミナルに貼り付けて実行してください。\n\n{command}")
            .format(command=command),
        )

    def open_config_folder(self):
        folder = self.target().config_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def install(self):
        target = self.target()
        path = target.config_path()
        registered = target.registered(path)
        question = (
            tr("既存の登録を新しい設定で置き換えます。よろしいですか？")
            if registered
            else tr("{client} の設定に登録します。よろしいですか？").format(
                client=target.label
            )
        )
        answer = QMessageBox.question(
            self,
            tr("MCP サーバー設定"),
            tr("{question}\n\n{path}\n\n他の MCP サーバーの設定はそのまま残し、"
               "上書きの前にバックアップ（.bak）を作ります。").format(
                question=question, path=path
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            report = target.install(
                self._project_argument(), allow_write=self.allow_write.isChecked()
            )
        except (OSError, ValueError) as error:
            log.warning("MCP の登録に失敗しました: %s", error)
            QMessageBox.warning(
                self,
                tr("MCP サーバー設定"),
                tr("登録できませんでした。\n\n{error}").format(error=error),
            )
            return

        lines = [
            tr("登録しました: {path}").format(path=report["config_path"]),
        ]
        if report["backup_path"]:
            lines.append(
                tr("バックアップ: {path}").format(path=report["backup_path"])
            )
        if report["other_servers"]:
            lines.append(
                tr("既存のサーバー設定は残しました: {names}").format(
                    names=", ".join(report["other_servers"])
                )
            )
        lines.append("")
        lines.append(
            tr("{client} を再起動すると使えるようになります。").format(
                client=target.label
            )
            if target.needs_restart
            else tr("次に {client} を起動したときから使えます。").format(
                client=target.label
            )
        )
        QMessageBox.information(
            self, tr("MCP サーバー設定"), "\n".join(lines)
        )
        self.refresh()
