from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QComboBox,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .models import WorkspaceProfile, build_profile
from .runtime import RuntimeManager
from .storage import load_profiles, log_dir_for_profile, save_profiles
from .theme import STYLESHEET


class RuntimeJob(QObject):
    finished = Signal(str, object, str)

    def __init__(self, runtime: RuntimeManager, profile: WorkspaceProfile, action: str) -> None:
        super().__init__()
        self.runtime = runtime
        self.profile = profile
        self.action = action

    def run(self) -> None:
        try:
            if self.action == "start":
                status = self.runtime.start(self.profile)
            else:
                status = self.runtime.stop(self.profile)
            self.finished.emit(self.action, status, "")
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(self.action, None, str(exc))


class MainWindow(QMainWindow):
    TUNNEL_OPTIONS = [
        ("frp", "FRP"),
        ("cloudflare", "Cloudflare"),
    ]
    CLOUDFLARE_MODE_OPTIONS = [
        ("quick", "临时隧道"),
        ("named", "固定域名"),
    ]
    AUTH_OPTIONS = [
        ("oauth", "OAuth"),
        ("bearer", "Bearer Token"),
        ("noauth", "不启用认证"),
    ]
    TOOL_PROFILE_OPTIONS = [
        ("full", "完整工具"),
        ("read-only", "只读工具"),
        ("compat-readonly-all", "兼容只读"),
    ]
    PERMISSION_MODE_OPTIONS = [
        ("trusted", "受信任"),
        ("safe", "安全受限"),
        ("dangerous", "完全放开"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Coding Tools MCP Desktop")
        self.resize(1460, 920)
        self.runtime = RuntimeManager()
        self.profiles = load_profiles()
        self.current_profile: WorkspaceProfile | None = None
        self._runtime_thread: QThread | None = None
        self._runtime_job: RuntimeJob | None = None
        self._busy_profile_id: str | None = None
        self._busy_action: str | None = None
        self._busy_dots = 0
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(350)
        self._busy_timer.timeout.connect(self._tick_busy_indicator)
        self._build_ui()
        self._populate_workspace_list()
        if self.profiles:
            self.workspace_list.setCurrentRow(0)
        else:
            self._clear_panel()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(18)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 18, 18, 18)
        sidebar_layout.setSpacing(14)

        eyebrow = QLabel("工作区控制台")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("MCP 桌面客户端")
        title.setObjectName("Title")
        subtitle = QLabel("围绕 Workspace 管理公网接入、认证方式和本地运行状态。")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color:#667085; font-size:14px;")

        actions = QHBoxLayout()
        add_button = QPushButton("添加工作区")
        add_button.clicked.connect(self._add_workspace)
        self.delete_button = QPushButton("删除")
        self.delete_button.setProperty("secondary", True)
        self.delete_button.clicked.connect(self._delete_workspace)
        refresh_button = QPushButton("刷新")
        refresh_button.setProperty("secondary", True)
        refresh_button.clicked.connect(self._refresh_current)
        actions.addWidget(add_button)
        actions.addWidget(self.delete_button)
        actions.addWidget(refresh_button)

        self.workspace_list = QListWidget()
        self.workspace_list.currentRowChanged.connect(self._on_workspace_selected)

        sidebar_layout.addWidget(eyebrow)
        sidebar_layout.addWidget(title)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addLayout(actions)
        sidebar_layout.addWidget(self.workspace_list, 1)

        panel = QFrame()
        panel.setObjectName("Panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(22, 22, 22, 22)
        panel_layout.setSpacing(16)

        self.header_title = QLabel("先添加一个工作区")
        self.header_title.setObjectName("Title")
        self.header_title.setStyleSheet("font-size:24px;")
        self.header_meta = QLabel("左侧添加工作区后，再配置公网地址和认证。")
        self.header_meta.setStyleSheet("color:#667085; font-size:13px;")

        header_actions = QHBoxLayout()
        self.start_button = QPushButton("启动")
        self.start_button.clicked.connect(self._start_runtime)
        self.stop_button = QPushButton("停止")
        self.stop_button.setProperty("secondary", True)
        self.stop_button.clicked.connect(self._stop_runtime)
        self.copy_button = QPushButton("复制 MCP 地址")
        self.copy_button.setProperty("secondary", True)
        self.copy_button.clicked.connect(self._copy_endpoint)
        self.copy_frp_button = QPushButton("复制 FRP 片段")
        self.copy_frp_button.setProperty("secondary", True)
        self.copy_frp_button.clicked.connect(self._copy_frp_snippet)
        header_actions.addWidget(self.start_button)
        header_actions.addWidget(self.stop_button)
        header_actions.addWidget(self.copy_button)
        header_actions.addWidget(self.copy_frp_button)
        header_actions.addStretch(1)

        content = QGridLayout()
        content.setHorizontalSpacing(16)
        content.setVerticalSpacing(16)

        self.workspace_group = self._build_workspace_group()
        self.runtime_group = self._build_runtime_group()
        self.auth_group = self._build_auth_group()
        self.log_group = self._build_log_group()

        content.addWidget(self.workspace_group, 0, 0)
        content.addWidget(self.runtime_group, 0, 1)
        content.addWidget(self.auth_group, 1, 0)
        content.addWidget(self.log_group, 1, 1)
        content.setColumnStretch(0, 1)
        content.setColumnStretch(1, 1)

        panel_layout.addWidget(self.header_title)
        panel_layout.addWidget(self.header_meta)
        panel_layout.addLayout(header_actions)
        panel_layout.addLayout(content, 1)

        layout.addWidget(sidebar, 1)
        layout.addWidget(panel, 2)
        self.setCentralWidget(root)
        self._wire_live_updates()

    def _build_workspace_group(self) -> QGroupBox:
        box = QGroupBox("工作区与公网入口")
        self.workspace_form = QFormLayout(box)

        self.name_edit = QLineEdit()
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.tunnel_type = QComboBox()
        self._fill_combo(self.tunnel_type, self.TUNNEL_OPTIONS)
        self.tunnel_type.currentIndexChanged.connect(self._refresh_tunnel_fields)

        self.public_url_label = QLabel("公网地址")
        self.public_url_edit = QLineEdit()
        self.public_url_edit.setPlaceholderText("Cloudflare 启动后会自动分配公网地址")
        self.cloudflare_mode_label = QLabel("Cloudflare 模式")
        self.cloudflare_mode = QComboBox()
        self._fill_combo(self.cloudflare_mode, self.CLOUDFLARE_MODE_OPTIONS)
        self.cloudflare_mode.currentIndexChanged.connect(self._refresh_tunnel_fields)
        self.cloudflare_token_label = QLabel("Tunnel Token")
        self.cloudflare_token_edit = QLineEdit()
        self.cloudflare_token_edit.setPlaceholderText("命名隧道模式下填写 Cloudflare Tunnel Token")

        self.frp_server_label = QLabel("FRP 服务器域名")
        self.frp_server_edit = QLineEdit()
        self.frp_server_edit.setPlaceholderText("例如：frp.example.com")

        self.subdomain_label = QLabel("FRP 子域名")
        self.subdomain_edit = QLineEdit()
        self.subdomain_edit.setPlaceholderText("例如：mcp")

        self.workspace_form.addRow("名称", self.name_edit)
        self.workspace_form.addRow("工作区路径", self.path_edit)
        self.workspace_form.addRow("隧道方式", self.tunnel_type)
        self.workspace_form.addRow(self.cloudflare_mode_label, self.cloudflare_mode)
        self.workspace_form.addRow(self.public_url_label, self.public_url_edit)
        self.workspace_form.addRow(self.cloudflare_token_label, self.cloudflare_token_edit)
        self.workspace_form.addRow(self.frp_server_label, self.frp_server_edit)
        self.workspace_form.addRow(self.subdomain_label, self.subdomain_edit)

        self.endpoint_hint = QLabel("当前入口：-")
        self.endpoint_hint.setWordWrap(True)
        self.endpoint_hint.setStyleSheet("color:#667085;")
        self.workspace_form.addRow("当前入口", self.endpoint_hint)

        save_button = QPushButton("保存配置")
        save_button.clicked.connect(self._save_current)
        self.workspace_form.addRow(save_button)
        return box

    def _build_runtime_group(self) -> QGroupBox:
        box = QGroupBox("运行时")
        form = QFormLayout(box)

        self.local_port = QSpinBox()
        self.local_port.setMaximum(65535)
        self.local_port.setMinimum(1000)

        self.tool_profile = QComboBox()
        self._fill_combo(self.tool_profile, self.TOOL_PROFILE_OPTIONS)

        self.permission_mode = QComboBox()
        self._fill_combo(self.permission_mode, self.PERMISSION_MODE_OPTIONS)

        self.runtime_command = QLineEdit()
        self.runtime_command.setPlaceholderText("可选，例如：coding-tools-mcp")

        self.status_label = QLabel("未启动")
        self.status_label.setStyleSheet("font-weight:700; color:#b42318;")

        form.addRow("本地端口", self.local_port)
        form.addRow("工具档位", self.tool_profile)
        form.addRow("权限模式", self.permission_mode)
        form.addRow("自定义命令", self.runtime_command)
        form.addRow("状态", self.status_label)
        return box

    def _build_auth_group(self) -> QGroupBox:
        box = QGroupBox("认证与 ChatGPT 接入")
        layout = QVBoxLayout(box)

        self.auth_form = QFormLayout()
        self.auth_type = QComboBox()
        self._fill_combo(self.auth_type, self.AUTH_OPTIONS)
        self.auth_type.currentIndexChanged.connect(self._refresh_auth_fields)

        self.oauth_client_id_label = QLabel("OAuth 客户端 ID")
        self.oauth_client_id = QLineEdit()

        self.oauth_client_secret_label = QLabel("OAuth 客户端密钥")
        self.oauth_client_secret = QLineEdit()

        self.oauth_password_label = QLabel("授权口令")
        self.oauth_password = QLineEdit()
        self.oauth_password.setPlaceholderText("ChatGPT 首次授权时输入这个口令")

        self.bearer_token_label = QLabel("Bearer Token")
        self.bearer_token = QLineEdit()

        self.auth_form.addRow("认证方式", self.auth_type)
        self.auth_form.addRow(self.oauth_client_id_label, self.oauth_client_id)
        self.auth_form.addRow(self.oauth_client_secret_label, self.oauth_client_secret)
        self.auth_form.addRow(self.oauth_password_label, self.oauth_password)
        self.auth_form.addRow(self.bearer_token_label, self.bearer_token)
        layout.addLayout(self.auth_form)

        self.oauth_actions = QWidget()
        oauth_actions_layout = QHBoxLayout(self.oauth_actions)
        oauth_actions_layout.setContentsMargins(0, 0, 0, 0)
        oauth_actions_layout.setSpacing(10)
        self.copy_client_id_button = QPushButton("复制客户端 ID")
        self.copy_client_id_button.setProperty("secondary", True)
        self.copy_client_id_button.clicked.connect(self._copy_oauth_client_id)
        self.copy_client_secret_button = QPushButton("复制客户端密钥")
        self.copy_client_secret_button.setProperty("secondary", True)
        self.copy_client_secret_button.clicked.connect(self._copy_oauth_client_secret)
        self.copy_oauth_password_button = QPushButton("复制授权口令")
        self.copy_oauth_password_button.setProperty("secondary", True)
        self.copy_oauth_password_button.clicked.connect(self._copy_oauth_password)
        oauth_actions_layout.addWidget(self.copy_client_id_button)
        oauth_actions_layout.addWidget(self.copy_client_secret_button)
        oauth_actions_layout.addWidget(self.copy_oauth_password_button)
        oauth_actions_layout.addStretch(1)
        layout.addWidget(self.oauth_actions)

        self.bearer_actions = QWidget()
        bearer_actions_layout = QHBoxLayout(self.bearer_actions)
        bearer_actions_layout.setContentsMargins(0, 0, 0, 0)
        bearer_actions_layout.setSpacing(10)
        self.copy_bearer_button = QPushButton("复制 Bearer Token")
        self.copy_bearer_button.setProperty("secondary", True)
        self.copy_bearer_button.clicked.connect(self._copy_bearer_token)
        bearer_actions_layout.addWidget(self.copy_bearer_button)
        bearer_actions_layout.addStretch(1)
        layout.addWidget(self.bearer_actions)

        self.auth_hint = QLabel("OAuth 模式下，ChatGPT 里填客户端 ID 和客户端密钥；首次授权时再输入授权口令。")
        self.auth_hint.setWordWrap(True)
        self.auth_hint.setStyleSheet("color:#667085;")
        layout.addWidget(self.auth_hint)
        return box

    def _build_log_group(self) -> QGroupBox:
        box = QGroupBox("日志与地址")
        layout = QVBoxLayout(box)
        self.endpoint_label = QLabel("公网 MCP 地址：-")
        self.local_label = QLabel("本地 MCP 地址：-")
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(220)
        layout.addWidget(self.endpoint_label)
        layout.addWidget(self.local_label)
        layout.addWidget(self.log_output)
        return box

    def _wire_live_updates(self) -> None:
        for widget in (
            self.name_edit,
            self.public_url_edit,
            self.cloudflare_token_edit,
            self.frp_server_edit,
            self.subdomain_edit,
            self.runtime_command,
            self.oauth_client_id,
            self.oauth_client_secret,
            self.oauth_password,
            self.bearer_token,
        ):
            widget.textChanged.connect(self._refresh_connection_view)
        self.local_port.valueChanged.connect(self._refresh_connection_view)
        self.tunnel_type.currentIndexChanged.connect(self._refresh_connection_view)
        self.auth_type.currentIndexChanged.connect(self._refresh_connection_view)

    def _populate_workspace_list(self) -> None:
        self.workspace_list.clear()
        for profile in self.profiles:
            item = QListWidgetItem(self._workspace_summary(profile))
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.workspace_list.addItem(item)

    def _on_workspace_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.profiles):
            self.current_profile = None
            self._clear_panel()
            return
        self.current_profile = self.profiles[row]
        self._load_profile(self.current_profile)

    def _load_profile(self, profile: WorkspaceProfile) -> None:
        self.header_title.setText(profile.name)
        self.header_meta.setText(profile.path)
        self.name_edit.setText(profile.name)
        self.path_edit.setText(profile.path)
        self._set_combo_value(self.cloudflare_mode, profile.tunnel.cloudflare_mode)
        self.public_url_edit.setText(self._profile_public_url_for_edit(profile))
        self.cloudflare_token_edit.setText(profile.tunnel.cloudflare_token)
        self.frp_server_edit.setText(profile.tunnel.frp_server)
        self.subdomain_edit.setText(profile.tunnel.frp_subdomain)
        self._set_combo_value(self.tunnel_type, profile.tunnel.type)
        self.local_port.setValue(profile.runtime.local_port)
        self._set_combo_value(self.tool_profile, profile.runtime.tool_profile)
        self._set_combo_value(self.permission_mode, profile.runtime.permission_mode)
        self.runtime_command.setText(profile.runtime.runtime_command)
        self._set_combo_value(self.auth_type, profile.auth.type)
        self.oauth_client_id.setText(profile.auth.oauth_client_id)
        self.oauth_client_secret.setText(profile.auth.oauth_client_secret)
        self.oauth_password.setText(profile.auth.oauth_password)
        self.bearer_token.setText(profile.auth.bearer_token)
        status = self.runtime.status(profile)
        self._render_status(status)
        self._load_logs(profile)
        self._refresh_tunnel_fields()
        self._refresh_auth_fields()
        self._refresh_connection_view()
        self._set_panel_enabled(True)

    def _clear_panel(self) -> None:
        self.header_title.setText("先添加一个工作区")
        self.header_meta.setText("左侧添加工作区后，再配置公网地址和认证。")
        self.name_edit.clear()
        self.path_edit.clear()
        self.public_url_edit.clear()
        self.cloudflare_token_edit.clear()
        self.frp_server_edit.clear()
        self.subdomain_edit.clear()
        self.oauth_client_id.clear()
        self.oauth_client_secret.clear()
        self.oauth_password.clear()
        self.bearer_token.clear()
        self.runtime_command.clear()
        self.local_port.setValue(28766)
        self._set_combo_value(self.tunnel_type, "frp")
        self._set_combo_value(self.cloudflare_mode, "quick")
        self._set_combo_value(self.tool_profile, "full")
        self._set_combo_value(self.permission_mode, "trusted")
        self._set_combo_value(self.auth_type, "oauth")
        self.status_label.setText("未启动")
        self.status_label.setStyleSheet("font-weight:700; color:#b42318;")
        self.endpoint_label.setText("公网 MCP 地址：-")
        self.local_label.setText("本地 MCP 地址：-")
        self.endpoint_hint.setText("当前入口：-")
        self.log_output.setPlainText("当前还没有日志。")
        self._refresh_tunnel_fields()
        self._refresh_auth_fields()
        self._set_panel_enabled(False)

    def _set_panel_enabled(self, enabled: bool) -> None:
        for widget in (
            self.workspace_group,
            self.runtime_group,
            self.auth_group,
            self.log_group,
            self.start_button,
            self.stop_button,
            self.copy_button,
            self.copy_frp_button,
            self.delete_button,
        ):
            widget.setEnabled(enabled)

    def _save_current(self) -> None:
        profile = self._require_profile()
        profile.name = self.name_edit.text().strip() or "工作区"
        profile.tunnel.type = self._combo_value(self.tunnel_type)
        profile.tunnel.cloudflare_mode = self._combo_value(self.cloudflare_mode)
        profile.tunnel.cloudflare_token = self.cloudflare_token_edit.text().strip()
        if profile.tunnel.type == "frp":
            profile.tunnel.public_url = self.public_url_edit.text().strip() or profile.tunnel.public_url
        elif profile.tunnel.cloudflare_mode == "named":
            profile.tunnel.public_url = self.public_url_edit.text().strip()
        else:
            profile.tunnel.public_url = ""
        profile.tunnel.frp_server = self.frp_server_edit.text().strip() or profile.tunnel.frp_server
        profile.tunnel.frp_subdomain = self.subdomain_edit.text().strip() or profile.tunnel.frp_subdomain
        profile.runtime.local_port = self.local_port.value()
        profile.runtime.tool_profile = self._combo_value(self.tool_profile)
        profile.runtime.permission_mode = self._combo_value(self.permission_mode)
        profile.runtime.runtime_command = self.runtime_command.text().strip()
        profile.auth.type = self._combo_value(self.auth_type)
        profile.auth.oauth_client_id = self.oauth_client_id.text().strip() or profile.auth.oauth_client_id
        profile.auth.oauth_client_secret = self.oauth_client_secret.text().strip()
        profile.auth.oauth_password = self.oauth_password.text().strip() or profile.auth.oauth_password
        profile.auth.bearer_token = self.bearer_token.text().strip() or profile.auth.bearer_token
        save_profiles(self.profiles)
        self._populate_workspace_list()
        self._restore_selection(profile.id)

    def _add_workspace(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择工作区目录")
        if not directory:
            return
        profile = build_profile(directory)
        self.profiles.append(profile)
        save_profiles(self.profiles)
        self._populate_workspace_list()
        self.workspace_list.setCurrentRow(len(self.profiles) - 1)

    def _delete_workspace(self) -> None:
        profile = self._require_profile()
        answer = QMessageBox.question(
            self,
            "删除工作区",
            f"确定删除工作区“{profile.name}”吗？\n这不会删除磁盘目录，只会从客户端配置里移除。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.runtime.stop(profile)
        current_index = self.workspace_list.currentRow()
        self.profiles = [item for item in self.profiles if item.id != profile.id]
        save_profiles(self.profiles)
        self.current_profile = None
        self._populate_workspace_list()
        if self.profiles:
            self.workspace_list.setCurrentRow(min(current_index, len(self.profiles) - 1))
        else:
            self._clear_panel()

    def _start_runtime(self) -> None:
        profile = self._require_profile()
        self._save_current()
        self._set_runtime_busy(True, "启动中")
        self._run_runtime_job(profile, "start")

    def _stop_runtime(self) -> None:
        profile = self._require_profile()
        self._set_runtime_busy(True, "停止中")
        self._run_runtime_job(profile, "stop")

    def _copy_endpoint(self) -> None:
        self._save_current()
        profile = self._require_profile()
        endpoint = self.runtime.resolved_endpoint(profile) or self._draft_endpoint()
        QApplication.clipboard().setText(endpoint)
        self.statusBar().showMessage("已复制 MCP 地址到剪贴板", 3000)

    def _copy_frp_snippet(self) -> None:
        self._save_current()
        profile = self._require_profile()
        QApplication.clipboard().setText(profile.frp_proxy_snippet())
        self.statusBar().showMessage("已复制 FRP 代理片段", 3000)

    def _copy_oauth_client_id(self) -> None:
        self._save_current()
        QApplication.clipboard().setText(self.oauth_client_id.text().strip())
        self.statusBar().showMessage("已复制 OAuth 客户端 ID", 3000)

    def _copy_oauth_client_secret(self) -> None:
        self._save_current()
        QApplication.clipboard().setText(self.oauth_client_secret.text().strip())
        self.statusBar().showMessage("已复制 OAuth 客户端密钥", 3000)

    def _copy_oauth_password(self) -> None:
        self._save_current()
        QApplication.clipboard().setText(self.oauth_password.text().strip())
        self.statusBar().showMessage("已复制授权口令", 3000)

    def _copy_bearer_token(self) -> None:
        self._save_current()
        QApplication.clipboard().setText(self.bearer_token.text().strip())
        self.statusBar().showMessage("已复制 Bearer Token", 3000)

    def _refresh_current(self) -> None:
        if self.current_profile is None:
            return
        self._load_profile(self.current_profile)

    def _refresh_tunnel_fields(self, *_args: object) -> None:
        tunnel_type = self._combo_value(self.tunnel_type)
        is_frp = tunnel_type == "frp"
        is_cloudflare = tunnel_type == "cloudflare"
        is_cloudflare_named = is_cloudflare and self._combo_value(self.cloudflare_mode) == "named"
        self._set_row_visible(self.cloudflare_mode_label, self.cloudflare_mode, is_cloudflare)
        self._set_row_visible(self.public_url_label, self.public_url_edit, is_cloudflare)
        self._set_row_visible(self.cloudflare_token_label, self.cloudflare_token_edit, is_cloudflare_named)
        self._set_row_visible(self.frp_server_label, self.frp_server_edit, is_frp)
        self._set_row_visible(self.subdomain_label, self.subdomain_edit, is_frp)
        self.public_url_edit.setReadOnly(is_cloudflare and not is_cloudflare_named)
        self.copy_frp_button.setEnabled(is_frp and self.current_profile is not None)
        if is_cloudflare_named:
            self.public_url_edit.setPlaceholderText("例如：https://mcp.example.com")
        elif is_cloudflare:
            self.public_url_edit.setPlaceholderText("Cloudflare 启动后会自动分配公网地址")
            if self.current_profile is not None and not self.runtime.resolved_public_url(self.current_profile):
                self.public_url_edit.setText("")
        self._refresh_connection_view()

    def _refresh_auth_fields(self, *_args: object) -> None:
        auth_type = self._combo_value(self.auth_type)
        is_oauth = auth_type == "oauth"
        is_bearer = auth_type == "bearer"
        self._set_row_visible(self.oauth_client_id_label, self.oauth_client_id, is_oauth)
        self._set_row_visible(self.oauth_client_secret_label, self.oauth_client_secret, is_oauth)
        self._set_row_visible(self.oauth_password_label, self.oauth_password, is_oauth)
        self._set_row_visible(self.bearer_token_label, self.bearer_token, is_bearer)
        self.oauth_actions.setVisible(is_oauth)
        self.bearer_actions.setVisible(is_bearer)
        if is_oauth:
            self.auth_hint.setText("ChatGPT 里填写 OAuth 客户端 ID 和 OAuth 客户端密钥；首次授权时再输入授权口令。")
        elif is_bearer:
            self.auth_hint.setText("Bearer 模式下，把这个 Token 配给调用方即可。")
        else:
            self.auth_hint.setText("当前不会要求认证，适合纯本地调试，不建议直接暴露到公网。")
        self._refresh_connection_view()

    def _refresh_connection_view(self, *_args: object) -> None:
        endpoint = self._draft_endpoint()
        self.endpoint_label.setText(f"公网 MCP 地址：{endpoint}")
        self.local_label.setText(f"本地 MCP 地址：http://127.0.0.1:{self.local_port.value()}/mcp")
        self.endpoint_hint.setText(f"当前入口：{endpoint}")

    def _load_logs(self, profile: WorkspaceProfile) -> None:
        log_dir = log_dir_for_profile(profile.id)
        output: list[str] = []
        for name in ("cloudflared.log", "stderr.log", "stdout.log"):
            path = log_dir / name
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="replace")
                output.append(f"[{name}]\n{text[-4000:]}")
        self.log_output.setPlainText("\n\n".join(output) if output else "当前还没有日志。")

    def _render_status(self, status) -> None:
        state_map = {
            "running": "运行中",
            "stopped": "已停止",
            "starting": "启动中",
            "error": "异常",
        }
        state_text = state_map.get(status.state, status.state)
        self.status_label.setText(f"{state_text}  PID={status.pid or '-'}")
        color = "#067647" if status.state == "running" else "#b42318"
        self.status_label.setStyleSheet(f"font-weight:700; color:{color};")

    def _run_runtime_job(self, profile: WorkspaceProfile, action: str) -> None:
        if self._runtime_thread is not None:
            return
        self._runtime_thread = QThread(self)
        self._runtime_job = RuntimeJob(self.runtime, profile, action)
        self._runtime_job.moveToThread(self._runtime_thread)
        self._runtime_thread.started.connect(self._runtime_job.run)
        self._runtime_job.finished.connect(self._on_runtime_job_finished)
        self._runtime_job.finished.connect(self._runtime_thread.quit)
        self._runtime_thread.finished.connect(self._cleanup_runtime_job)
        self._runtime_thread.start()

    def _on_runtime_job_finished(self, action: str, status: object, error_message: str) -> None:
        profile = self.current_profile
        self._set_runtime_busy(False)
        if profile is None:
            return
        if error_message:
            self._load_logs(profile)
            self._refresh_workspace_item(profile.id)
            QMessageBox.critical(self, "启动失败" if action == "start" else "停止失败", error_message)
            return
        if status is not None:
            self._render_status(status)
        self._sync_profile_runtime_view(profile)
        self._load_logs(profile)
        self._refresh_workspace_item(profile.id)

    def _cleanup_runtime_job(self) -> None:
        if self._runtime_job is not None:
            self._runtime_job.deleteLater()
            self._runtime_job = None
        if self._runtime_thread is not None:
            self._runtime_thread.deleteLater()
            self._runtime_thread = None

    def _set_runtime_busy(self, busy: bool, state_text: str | None = None) -> None:
        self.start_button.setEnabled(not busy and self.current_profile is not None)
        self.stop_button.setEnabled(not busy and self.current_profile is not None)
        self.workspace_list.setEnabled(not busy)
        if busy:
            profile = self.current_profile
            self._busy_profile_id = profile.id if profile is not None else None
            self._busy_action = state_text
            self._busy_dots = 0
            self.start_button.setText("启动中..." if state_text == "启动中" else "启动")
            self.stop_button.setText("停止中..." if state_text == "停止中" else "停止")
            if state_text:
                self.status_label.setText(f"{state_text}  PID=-")
                self.status_label.setStyleSheet("font-weight:700; color:#b54708;")
            self.statusBar().showMessage(f"{state_text}，请稍候...", 0)
            if not self._busy_timer.isActive():
                self._busy_timer.start()
            if self._busy_profile_id:
                self._refresh_workspace_item(self._busy_profile_id)
            return
        self._busy_timer.stop()
        self._busy_profile_id = None
        self._busy_action = None
        self._busy_dots = 0
        self.start_button.setText("启动")
        self.stop_button.setText("停止")
        self.statusBar().clearMessage()

    def _tick_busy_indicator(self) -> None:
        if self._busy_action is None:
            return
        self._busy_dots = (self._busy_dots + 1) % 4
        dots = "." * self._busy_dots
        label = f"{self._busy_action}{dots}"
        self.status_label.setText(f"{label}  PID=-")
        self.status_label.setStyleSheet("font-weight:700; color:#b54708;")
        if self._busy_profile_id:
            self._refresh_workspace_item(self._busy_profile_id)

    def _sync_profile_runtime_view(self, profile: WorkspaceProfile) -> None:
        if profile.tunnel.type == "cloudflare":
            public_url = self.runtime.resolved_public_url(profile)
            if public_url:
                self.public_url_edit.setText(public_url)
            elif profile.tunnel.cloudflare_mode != "named":
                self.public_url_edit.clear()
        self._refresh_connection_view()

    def _refresh_workspace_item(self, profile_id: str) -> None:
        for index, profile in enumerate(self.profiles):
            if profile.id != profile_id:
                continue
            item = self.workspace_list.item(index)
            if item is not None:
                item.setText(self._workspace_summary(profile))
            break

    def _draft_public_url(self) -> str:
        tunnel_type = self._combo_value(self.tunnel_type)
        if tunnel_type == "frp":
            subdomain = self.subdomain_edit.text().strip()
            server = self.frp_server_edit.text().strip()
            if subdomain and server:
                return f"https://{subdomain}.{server}"
        if tunnel_type == "cloudflare":
            if self.current_profile is not None:
                resolved = self.runtime.resolved_public_url(self.current_profile)
                if resolved:
                    return resolved
            if self._combo_value(self.cloudflare_mode) == "named":
                return self.public_url_edit.text().strip().rstrip("/")
            return ""
        return self.public_url_edit.text().strip().rstrip("/")

    def _draft_endpoint(self) -> str:
        base_url = self._draft_public_url().rstrip("/")
        if not base_url:
            return "-"
        return f"{base_url}/mcp"

    def _workspace_summary(self, profile: WorkspaceProfile) -> str:
        state = self._workspace_state(profile)
        endpoint = self._profile_endpoint_summary(profile)
        state_map = {
            "running": "运行中",
            "stopped": "已停止",
            "starting": "启动中",
            "error": "异常",
            "stopping": "停止中",
        }
        return "\n".join(
            [
                profile.name,
                profile.path,
                f"隧道：{self._label_for_value(self.TUNNEL_OPTIONS, profile.tunnel.type)}  认证：{self._label_for_value(self.AUTH_OPTIONS, profile.auth.type)}",
                f"状态：{state_map.get(state, state)}  地址：{endpoint or '-'}",
            ]
        )

    def _restore_selection(self, profile_id: str) -> None:
        for index, profile in enumerate(self.profiles):
            if profile.id == profile_id:
                self.workspace_list.setCurrentRow(index)
                return

    def _require_profile(self) -> WorkspaceProfile:
        if self.current_profile is None:
            raise RuntimeError("当前没有选中工作区。")
        return self.current_profile

    def _fill_combo(self, combo: QComboBox, options: list[tuple[str, str]]) -> None:
        for value, label in options:
            combo.addItem(label, value)

    def _combo_value(self, combo: QComboBox) -> str:
        return str(combo.currentData())

    def _set_combo_value(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _label_for_value(self, options: list[tuple[str, str]], value: str) -> str:
        for item_value, item_label in options:
            if item_value == value:
                return item_label
        return value

    def _set_row_visible(self, label: QLabel, field: QWidget, visible: bool) -> None:
        label.setVisible(visible)
        field.setVisible(visible)

    def _profile_public_url_for_edit(self, profile: WorkspaceProfile) -> str:
        if profile.tunnel.type == "frp":
            return profile.tunnel.public_url
        resolved = self.runtime.resolved_public_url(profile)
        if resolved:
            return resolved
        if profile.tunnel.cloudflare_mode == "named":
            return profile.tunnel.public_url
        return ""

    def _profile_endpoint_summary(self, profile: WorkspaceProfile) -> str:
        endpoint = self.runtime.resolved_endpoint(profile)
        if endpoint:
            return endpoint
        if profile.tunnel.type == "frp":
            return profile.endpoint
        if profile.tunnel.type == "cloudflare" and profile.tunnel.cloudflare_mode == "named" and profile.tunnel.public_url.strip():
            return f"{profile.tunnel.public_url.rstrip('/')}/mcp"
        return "-"

    def _workspace_state(self, profile: WorkspaceProfile) -> str:
        if self._busy_profile_id == profile.id and self._busy_action == "启动中":
            return "starting"
        if self._busy_profile_id == profile.id and self._busy_action == "停止中":
            return "stopping"
        return self.runtime.summary_state(profile)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()

    def _present_window() -> None:
        screen = app.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            frame = window.frameGeometry()
            frame.moveCenter(available.center())
            window.move(frame.topLeft())
        window.setWindowState((window.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        window.raise_()
        window.activateWindow()

    QTimer.singleShot(0, _present_window)
    return app.exec()
