"""Collection management embedded in the main shelf, never a second window."""
from PySide6.QtCore import Signal,Qt,QTimer
from PySide6.QtGui import QColor,QIcon,QPixmap
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QListWidget,QListWidgetItem,QComboBox,QAbstractItemView,QMenu,QMessageBox

class BoardPanel(QWidget):
    selected=Signal(object)
    changed=Signal()
    def __init__(self,storage,parent=None):
        super().__init__(parent);self.storage=storage;self.current=None;self.setFixedWidth(240)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,16,0);layout.setSpacing(10)
        title=QLabel('Tus colecciones');title.setObjectName('sectionTitle');layout.addWidget(title)
        self.boards=QListWidget();self.boards.setObjectName("collections");self.boards.setAccessibleName('Colecciones');self.boards.currentItemChanged.connect(self.choose);layout.addWidget(self.boards,1)
        self.boards.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.boards.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.boards.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.boards.model().rowsMoved.connect(lambda *args:QTimer.singleShot(0,self.persist_order))
        self.boards.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.boards.customContextMenuRequested.connect(self.context_menu)
        self.editor=QWidget();editor_layout=QVBoxLayout(self.editor);editor_layout.setContentsMargins(0,0,0,0);editor_layout.setSpacing(8)
        manage=QPushButton('Administrar colecciones');manage.setObjectName('manageCollections');manage.setCheckable(True);manage.toggled.connect(self.editor.setVisible);layout.addWidget(manage)
        layout.addWidget(self.editor);self.editor.hide()
        self.name=QLineEdit();self.name.setPlaceholderText('Nueva colección…');self.name.setAccessibleName('Nombre de colección');editor_layout.addWidget(self.name)
        actions=QHBoxLayout()
        create=QPushButton('Crear');create.clicked.connect(self.create);actions.addWidget(create)
        rename=QPushButton('Renombrar');rename.clicked.connect(self.rename);actions.addWidget(rename);editor_layout.addLayout(actions)
        options=QHBoxLayout()
        self.color=QComboBox();self.color.setAccessibleName('Color de la colección')
        for name,value in [('Azul','#4F8CFF'),('Menta','#39B982'),('Violeta','#B28CFF'),('Ámbar','#E6B45A')]:self.color.addItem(name,value)
        self.color.activated.connect(self.recolor);options.addWidget(self.color,1)
        self.symbol=QLineEdit();self.symbol.setMaximumWidth(54);self.symbol.setMaxLength(8)
        self.symbol.setPlaceholderText('Icono');self.symbol.setAccessibleName('Símbolo personalizado de colección')
        self.symbol.setToolTip('Símbolo opcional de la colección; vacío usa el indicador de color')
        self.symbol.editingFinished.connect(self.resymbol);options.addWidget(self.symbol)
        for label,direction in [('↑',-1),('↓',1)]:
            button=QPushButton(label);button.setFixedWidth(28);button.setToolTip('Mover colección arriba' if direction<0 else 'Mover colección abajo')
            button.clicked.connect(lambda checked=False,d=direction:self.move(d));options.addWidget(button)
        editor_layout.addLayout(options)
        self.refresh()
    def refresh(self):
        self.boards.blockSignals(True);self.boards.clear()
        counts=self.storage.pinboard_counts()
        for board in self.storage.list_pinboards():
            item=QListWidgetItem(f'{board.display_name}  ·  {counts.get(board.id,0)}');item.setData(Qt.ItemDataRole.UserRole,board.id)
            dot=QPixmap(12,12);dot.fill(QColor(board.color_hex));item.setIcon(QIcon(dot));self.boards.addItem(item)
            if board.id==self.current:self.boards.setCurrentItem(item)
        self.boards.blockSignals(False)
        board=self.storage.get_pinboard(self.current) if self.current is not None else None
        if board:
            index=self.color.findData(board.color_hex)
            if index<0:
                self.color.addItem('Personalizado',board.color_hex);index=self.color.count()-1
            self.color.setCurrentIndex(index)
            self.symbol.setText(board.emoji or '')
    def choose(self,item,previous):
        if item:
            self.current=item.data(Qt.ItemDataRole.UserRole);self.selected.emit(self.current)
    def recolor(self,index):
        if self.current is None:return
        self.storage.update_pinboard(self.current,color_hex=self.color.itemData(index));self.refresh();self.changed.emit()
    def resymbol(self):
        if self.current is None:return
        self.storage.update_pinboard(self.current,emoji=self.symbol.text().strip());self.refresh();self.changed.emit()
    def persist_order(self):
        self.storage.reorder_pinboards([self.boards.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.boards.count())])
        self.refresh();self.changed.emit()
    def context_menu(self,pos):
        item=self.boards.itemAt(pos)
        if item is None:return
        self.current=item.data(Qt.ItemDataRole.UserRole)
        menu=QMenu(self);edit=menu.addAction('Editar colección');remove=menu.addAction('Eliminar colección…')
        action=menu.exec(self.boards.viewport().mapToGlobal(pos))
        if action==edit:
            board=self.storage.get_pinboard(self.current)
            if board:self.name.setText(board.name)
            self.editor.show();self.name.setFocus()
        elif action==remove:
            answer=QMessageBox.question(self,'Eliminar colección','Se elimina la colección, pero sus clips permanecen en History.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
            if answer!=QMessageBox.StandardButton.Yes:return
            self.storage.delete_pinboard(self.current);self.current=None;self.refresh();self.selected.emit(None);self.changed.emit()
    def move(self,direction):
        if self.current is None:return
        self.storage.move_pinboard(self.current,direction);self.refresh();self.changed.emit()
    def create(self):
        name=self.name.text().strip()
        if not name:return
        board=self.storage.create_pinboard(name);self.current=board.id;self.name.clear();self.refresh();self.selected.emit(board.id);self.changed.emit()
    def rename(self):
        name=self.name.text().strip()
        if not name or self.current is None:return
        self.storage.update_pinboard(self.current,name=name);self.name.clear();self.refresh();self.changed.emit()
