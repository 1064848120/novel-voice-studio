"""Small editors for reusable voice assets and character portraits."""
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QComboBox, QLabel
from .casting import GENDERS, AGES, TIMBRES


def edit_traits(parent, values, asset=False):
    dialog = QDialog(parent)
    dialog.setWindowTitle('音色档案' if asset else '人物特征')
    dialog.resize(460, 400)
    layout = QFormLayout(dialog)
    fields = {}
    if asset:
        fields['name'] = QLineEdit(values.get('name', ''))
        layout.addRow('音色名称', fields['name'])
    for key, label, options in [('gender', '声线性别', GENDERS), ('age', '年龄感', AGES), ('timbre', '音色特点', TIMBRES)]:
        widget = QComboBox()
        widget.addItems(options)
        widget.setCurrentText(values.get(key, '未指定'))
        fields[key] = widget
        layout.addRow(label, widget)
    if asset:
        role = QComboBox()
        role.addItems(['角色', '旁白'])
        role.setCurrentText(values.get('role', '角色'))
        fields['role'] = role
        layout.addRow('用途', role)
    for key, label in [('personality', '性格 / 表达风格'), ('description', '人物或音色说明')]:
        fields[key] = QLineEdit(values.get(key, ''))
        layout.addRow(label, fields[key])
    if asset:
        fields['license'] = QLineEdit(values.get('license', ''))
        layout.addRow('素材来源 / 授权说明', fields['license'])
        fields['reference_transcript'] = QLineEdit(values.get('reference_transcript', ''))
        layout.addRow('录音准确文字稿', fields['reference_transcript'])
    note = QLabel('修改人物特征不会更换已绑定的声音。' if not asset else '音色仅需入库一次，后续人物自动匹配；请使用你有权使用的单人清晰录音。')
    note.setWordWrap(True)
    layout.addRow(note)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addRow(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    result = {key: widget.currentText() if isinstance(widget, QComboBox) else widget.text().strip()
              for key, widget in fields.items()}
    if asset and not result['name']:
        result['name'] = '未命名音色'
    return result
