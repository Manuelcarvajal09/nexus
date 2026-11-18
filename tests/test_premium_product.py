from unittest.mock import patch
from PySide6.QtCore import Qt,QRect
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QStyleOptionViewItem,QListView
from nexus_linux import desktop,paths
from nexus_linux.timeline_window import TimelineWindow
from nexus_linux.history_delegate import HistoryDelegate

def test_history_is_horizontal_resizable_filtered(qapp,storage,paste_service):
    storage.capture(kind='text',text_content='Unicode español 日本語 ✓')
    storage.capture(kind='file',file_path='/synthetic/file.txt')
    window=TimelineWindow(storage,paste_service)
    assert window._list.flow()==QListView.Flow.LeftToRight
    assert window._list.viewMode()==QListView.ViewMode.IconMode
    assert not window._list.isWrapping()
    assert window._list.minimumHeight()<window._list.maximumHeight()
    window._set_kind('file')
    assert window._model.rowCount()==1
    assert window._model.item_at(0).kind=='file'
    window._set_kind(None)
    assert window._model.rowCount()==2
    window.close()

def test_geometry_saved_from_compositor_restores(qapp,tmp_path):
    from PySide6.QtWidgets import QWidget
    import os
    window=QWidget();window.setWindowTitle('Test History');window.show()
    clients=[{'pid':os.getpid(),'title':window.windowTitle(),'at':[200,140],'size':[420,620],'monitor':3}]
    monitors=[{'id':3,'name':'TEST'}]
    class Screen:
        def name(self):return 'TEST'
        def availableGeometry(self):return QRect(0,0,1920,1080)
    with patch.object(paths,'CONFIG_DIR',tmp_path),patch.object(desktop,'active_screen',return_value=Screen()),patch.dict(os.environ,HYPRLAND_INSTANCE_SIGNATURE='test'):
        desktop.save(window,clients,monitors)
        assert desktop.restore(window,(420,620))
        assert window._nexus_requested_geometry==QRect(200,140,420,620)
    window.close()

def test_vertical_delegate_paints_unicode(qapp,storage,paste_service):
    storage.capture(kind='text',text_content='España 日本語 ✓\nSecond line')
    window=TimelineWindow(storage,paste_service)
    image=QImage(420,100,QImage.Format.Format_ARGB32);image.fill(0)
    painter=QPainter(image);option=QStyleOptionViewItem();option.rect=QRect(0,0,400,88)
    window._delegate.paint(painter,option,window._model.index(0,0));painter.end()
    window.close()

def test_pinboards_share_main_surface_and_preserve_membership(qapp,storage,paste_service):
    item=storage.capture(kind='text',text_content='Snippet sintético')
    board=storage.create_pinboard('Trabajo')
    storage.add_to_pinboard(item.id,board.id)
    window=TimelineWindow(storage,paste_service);window.show()
    before=set(qapp.topLevelWidgets())
    window.show_pinboards()
    assert set(qapp.topLevelWidgets())==before
    assert window._model.rowCount()==1
    assert window._model.item_at(0).id==item.id
    panel=window._board_panel
    panel.name.setText('Renombrada');panel.rename()
    assert storage.get_pinboard(board.id).name=='Renombrada'
    assert storage.pinboard_counts()[board.id]==1
    window.show_history()
    assert not panel.isVisible()
    assert window._model.rowCount()==1
    window.close()

def test_new_shelf_bottom_placement_and_saved_geometry(qapp,storage,paste_service,tmp_path):
    window=TimelineWindow(storage,paste_service)
    class Screen:
        def name(self):return 'TEST'
        def availableGeometry(self):return QRect(100,50,1920,1000)
    with patch.object(paths,'CONFIG_DIR',tmp_path),patch.object(desktop,'active_screen',return_value=Screen()):
        assert desktop.restore_shelf(window)
        rect=window._nexus_requested_geometry
        assert rect.width()>rect.height()*3
        assert rect.bottom()==1017
        assert rect.left()>=132
    window.close()

def test_shelf_cards_fit_minimum_and_wide_layouts(qapp,storage,paste_service):
    storage.capture(kind='text',text_content='Synthetic clip')
    storage.create_pinboard('Synthetic board')
    window=TimelineWindow(storage,paste_service);window.show()
    for width,height in [(760,380),(1200,390),(1600,420)]:
        window.resize(width,height);qapp.processEvents()
        assert window._list.height()>=218
        assert window._search.geometry().right()<window.width()
        window.show_pinboards();qapp.processEvents()
        assert window._board_panel.window() is window
        assert window._board_panel.isVisible()
        window.show_history()
    window.close()
