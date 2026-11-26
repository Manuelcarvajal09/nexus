"""Dispatch policy with durable recovery, explicitly NOT application receipt."""
import logging
import re
import time

log = logging.getLogger('nexus.stack.sequential')


class SequentialStack:
    def __init__(self, stack, transport, changed=lambda:None):
        self.stack, self.transport, self.changed = stack, transport, changed
        self.enabled = False
        self.busy = False
        self.error = ''
        self.blocked = False
        self.sent_count = 0
        self.db = stack.storage._conn
        self.db.execute('''CREATE TABLE IF NOT EXISTS stack_deliveries (
            request_id TEXT PRIMARY KEY, item_id INTEGER REFERENCES clip_items(id) ON DELETE SET NULL,
            target TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL)''')
        self.db.execute("UPDATE stack_deliveries SET status='uncertain' WHERE status='pending'")
        self.db.commit()
        self.blocked = self.db.execute("SELECT 1 FROM stack_deliveries WHERE status='uncertain' LIMIT 1").fetchone() is not None
        if self.blocked:
            self.error = 'Entrega anterior incierta. Revisa el destino y pulsa Reintentar pendiente.'

    def enable(self):
        try:
            self.transport.install()
            self.enabled = True
            if not self.blocked:
                self.error = ''
        except (OSError, RuntimeError, ValueError) as exc:
            self.enabled = False
            self.error = str(exc)
        self.changed()
        return self.enabled

    def pause(self):
        self.enabled = False
        try:
            self.transport.remove()
        except (OSError, RuntimeError, ValueError) as exc:
            self.error = 'No se pudo retirar binding: '+str(exc)
            log.error(self.error)
        self.changed()

    def status(self):
        items = self.stack.items()
        if self.stack.reversed_order:
            items.reverse()
        return {'policy':'hyprland-dispatch-with-recovery', 'application_receipt':False,
                'active':self.stack.active, 'enabled':self.enabled, 'blocked':self.blocked,
                'order':'LIFO' if self.stack.reversed_order else 'FIFO',
                'pending_ids':[clip.id for clip in items], 'error':self.error,
                'journal_counts':dict(self.db.execute('SELECT status,COUNT(*) FROM stack_deliveries GROUP BY status'))}

    def dispatch(self, request, target):
        if not re.fullmatch(r'[a-f0-9]{32}:[0-9]+',request):
            return {'outcome':'failed','reason':'Petición inválida'}
        previous = self.db.execute('SELECT status FROM stack_deliveries WHERE request_id=?',(request,)).fetchone()
        if previous:
            return {'outcome':'duplicate','previous':previous[0]}
        if not self.enabled or not self.stack.active or not self.stack.items():
            return {'outcome':'inactive'}
        if self.busy or self.blocked:
            return {'outcome':'blocked','reason':self.error or 'Entrega pendiente'}
        self.busy = True
        item = self.stack.items()[-1 if self.stack.reversed_order else 0]
        try:
            if not self.transport.target_valid(target):
                raise RuntimeError('Foco cambió o destino no válido; no se envió')
            # Journal before writing or dispatching. Queue stays intact until
            # dispatched + removed are committed in one SQLite transaction.
            with self.db:
                self.db.execute('INSERT INTO stack_deliveries VALUES (?,?,?,?,?)',
                    (request,item.id,target,'pending',time.time()))
            if not self.stack.paste_service.write_to_clipboard(item):
                raise RuntimeError('No se pudo preparar el contenido; el elemento permanece')
            self.stack._armed_id = item.id
            self.stack.pending_confirmation = item.id
            self.changed()
            if not self.transport.target_valid(target):
                raise RuntimeError('Foco cambió durante preparación; no se envió')
            # An exception during send is ambiguous: preserve the queue and
            # require explicit retry rather than accidentally send twice.
            try:
                accepted = self.transport.send(target)
            except Exception as exc:
                self.blocked = True
                with self.db:
                    self.db.execute("UPDATE stack_deliveries SET status='uncertain' WHERE request_id=?",(request,))
                raise RuntimeError('Envío incierto; revisa destino antes de reintentar') from exc
            if not accepted:
                raise RuntimeError('Compositor rechazó el envío; el elemento permanece')
            remaining = [clip for clip in self.stack.items() if clip.id != item.id]
            try:
                with self.db:
                    self.stack.storage.save_stack_state([clip.id for clip in remaining],self.stack.reversed_order,commit=False)
                    self.db.execute("UPDATE stack_deliveries SET status='dispatched' WHERE request_id=?",(request,))
            except Exception:
                self.blocked = True
                raise RuntimeError('Envío aceptado pero persistencia incierta; no repitas sin revisar')
            self.stack._items = remaining
            self.sent_count += 1
            self.stack.pending_confirmation = None
            self.error = ''
            log.info('Stack: envío aceptado item=%s request=%s restantes=%s (sin acuse de aplicación)',item.id,request,len(remaining))
            if not remaining:
                self.stack.active = False
                self.pause()
            # Logical head is ready. Leave the delivered payload on clipboard
            # until the NEXT request; eager writes race delayed application reads.
            return {'outcome':'dispatched','item_id':item.id,'remaining':len(remaining),'application_receipt':False}
        except Exception as exc:
            self.error = str(exc)
            if not self.blocked:
                with self.db:
                    self.db.execute("UPDATE stack_deliveries SET status='failed' WHERE request_id=?",(request,))
            log.warning('Stack no avanza: %s',self.error)
            return {'outcome':'uncertain' if self.blocked else 'failed','reason':self.error}
        finally:
            self.busy = False
            self.changed()

    def retry_pending(self):
        # User explicitly accepts possible duplicate insertion after reviewing
        # an uncertain delivery. No automatic crash replay.
        with self.db:
            self.db.execute("UPDATE stack_deliveries SET status='retry-authorized' WHERE status IN ('uncertain','pending')")
        self.blocked = False
        self.error = ''
        self.enable()

    def restore_last(self):
        row = self.db.execute("SELECT request_id,item_id FROM stack_deliveries WHERE status='dispatched' ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return False
        item = self.stack.storage.get(row[1]) if row[1] else None
        if item is None or any(clip.id == item.id for clip in self.stack.items()):
            return False
        items = self.stack.items()
        items.insert(len(items) if self.stack.reversed_order else 0,item)
        with self.db:
            self.stack.storage.save_stack_state([clip.id for clip in items],self.stack.reversed_order,commit=False)
            self.db.execute("UPDATE stack_deliveries SET status='restored' WHERE request_id=?",(row[0],))
        self.stack._items = items
        self.stack.activate()
        self.enable()
        return True
