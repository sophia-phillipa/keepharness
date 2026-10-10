"""Row access for the agent service tables; statements are verbatim and never commit."""

import json


class ConversationRepository:
    """Jobs (conversation turns), conversation titles, deletions and approval rules."""

    def __init__(self, db):
        self.db = db

    def get(self, job):
        return self.db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()

    def owner(self, job):
        return self.db.execute("SELECT owner FROM jobs WHERE id=?", (job,)).fetchone()

    def payload(self, job):
        return self.db.execute("SELECT payload FROM jobs WHERE id=?", (job,)).fetchone()

    def state(self, job):
        return self.db.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()

    def turn(self, job, project, owner):
        return self.db.execute(
            "SELECT * FROM jobs WHERE id=? AND project=? AND owner=?", (job, project, owner)
        ).fetchone()

    def owned(self, owner, projects):
        marks = ",".join("?" for _ in projects)
        # has_turn_edits: one indexed EXISTS per job (events_job_terminal), no extra round trip.
        return self.db.execute(
            "SELECT jobs.*, EXISTS(SELECT 1 FROM events WHERE job=jobs.id AND type='turn_edit') "
            f"AS has_turn_edits FROM jobs WHERE owner=? AND project IN ({marks}) ORDER BY created,id",
            [owner, *projects],
        ).fetchall()

    def history(self, owner, projects):
        placeholders = ",".join("?" for _ in projects)
        return self.db.execute(
            f"SELECT id,project,state,created,payload FROM jobs WHERE owner=? AND project IN ({placeholders}) ORDER BY created DESC LIMIT 50",
            [owner, *projects],
        ).fetchall()

    def running(self):
        return self.db.execute("SELECT id FROM jobs WHERE state='running'").fetchall()

    def pending(self):
        return self.db.execute("SELECT * FROM jobs WHERE state IN ('queued','running')").fetchall()

    def project_busy(self, project):
        return self.db.execute(
            "SELECT 1 FROM jobs WHERE project=? AND state IN ('queued','running') LIMIT 1",
            (project,),
        ).fetchone()

    def ready(self):
        """Dispatch conversation turns only after their preceding turn finishes.

        A follow-up held after Stop waits until the user runs it (D16).
        """
        return self.db.execute(
            "SELECT child.* FROM jobs child LEFT JOIN jobs parent "
            "ON parent.id=json_extract(child.payload,'$.parent_job_id') "
            "WHERE child.state='queued' AND (parent.id IS NULL OR parent.state NOT IN ('queued','running')) "
            "AND json_extract(child.payload,'$._held_after_stop') IS NULL "
            "ORDER BY child.created,child.id"
        ).fetchall()

    def queued_followups(self, job):
        return self.db.execute(
            "SELECT id,payload FROM jobs WHERE state='queued' "
            "AND json_extract(payload,'$.parent_job_id')=? ORDER BY created,id",
            (job,),
        ).fetchall()

    def by_idempotency_key(self, owner, project, idem):
        return self.db.execute(
            "SELECT id,digest FROM jobs WHERE owner=? AND project=? AND idem=?",
            (owner, project, idem),
        ).fetchone()

    def count_pending(self):
        return self.db.execute(
            "SELECT count(*) FROM jobs WHERE state IN ('queued','running')"
        ).fetchone()[0]

    def count_for_project(self, project):
        return self.db.execute("SELECT count(*) FROM jobs WHERE project=?", (project,)).fetchone()[
            0
        ]

    def count_pending_for_owner(self, owner):
        return self.db.execute(
            "SELECT count(*) FROM jobs WHERE owner=? AND state IN ('queued','running')",
            (owner,),
        ).fetchone()[0]

    def insert(
        self, job, project, owner, state, created, payload, result, idem, digest, work_item=None
    ):
        self.db.execute(
            "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest,work_item) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job, project, owner, state, created, payload, result, idem, digest, work_item),
        )

    def activity(self, owner, projects, work_item=None):
        marks = ",".join("?" for _ in projects)
        query = f"SELECT * FROM jobs WHERE owner=? AND project IN ({marks})"
        parameters = [owner, *projects]
        if work_item is not None:
            query += " AND work_item=?"
            parameters.append(work_item)
        return self.db.execute(query + " ORDER BY created DESC,id", parameters).fetchall()

    def set_work_item(self, job, work_item):
        self.db.execute("UPDATE jobs SET work_item=? WHERE id=?", (work_item, job))

    def set_payload(self, job, payload):
        self.db.execute("UPDATE jobs SET payload=? WHERE id=?", (payload, job))

    def set_result(self, job, state, result):
        self.db.execute("UPDATE jobs SET state=?,result=? WHERE id=?", (state, result, job))

    def set_running(self, job):
        self.db.execute("UPDATE jobs SET state='running' WHERE id=?", (job,))

    def title(self, conversation):
        return self.db.execute(
            "SELECT title FROM conversation_titles WHERE id=?", (conversation,)
        ).fetchone()

    def titles(self):
        return dict(self.db.execute("SELECT id,title FROM conversation_titles"))

    def set_title(self, conversation, title):
        self.db.execute(
            "INSERT INTO conversation_titles(id,title) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title",
            (conversation, title),
        )

    # ``deleted_conversations`` keeps its original name: it holds archived (hidden, restorable)
    # conversations, exactly what a "deleted" one was, so older releases read it the same way.
    def is_archived(self, conversation):
        return self.db.execute(
            "SELECT 1 FROM deleted_conversations WHERE id=?", (conversation,)
        ).fetchone()

    def archived(self):
        return {r[0] for r in self.db.execute("SELECT id FROM deleted_conversations")}

    def archive(self, conversation):
        self.db.execute("INSERT OR IGNORE INTO deleted_conversations VALUES(?)", (conversation,))

    def unarchive(self, conversation):
        self.db.execute("DELETE FROM deleted_conversations WHERE id=?", (conversation,))

    def purge(self, owner, conversation, jobs):
        """Remove a conversation's turns and everything keyed by them (decision D31)."""
        marks = ",".join("?" for _ in jobs)
        for statement in (
            f"DELETE FROM events WHERE job IN ({marks})",
            f"DELETE FROM gates WHERE job_id IN ({marks})",
            f"DELETE FROM effects WHERE job_id IN ({marks})",
            f"DELETE FROM jobs WHERE id IN ({marks})",
        ):
            self.db.execute(statement, jobs)
        self.db.execute(
            "DELETE FROM approval_rules WHERE owner=? AND conversation=?", (owner, conversation)
        )
        self.db.execute("DELETE FROM conversation_titles WHERE id=?", (conversation,))
        self.unarchive(conversation)

    def has_approval_rule(self, scope):
        return self.db.execute(
            "SELECT 1 FROM approval_rules WHERE owner=? AND conversation=? AND backend=? AND model=? AND fingerprint=?",
            scope,
        ).fetchone()

    def add_approval_rule(self, scope):
        self.db.execute("INSERT OR IGNORE INTO approval_rules VALUES(?,?,?,?,?)", scope)

    def clear_approval_rules(self, owner, conversation):
        self.db.execute(
            "DELETE FROM approval_rules WHERE owner=? AND conversation=?",
            (owner, conversation),
        )

    def clear_model_approval_rules(self, scopes):
        self.db.executemany(
            "DELETE FROM approval_rules WHERE backend=? AND model=?",
            scopes,
        )


class MessageRepository:
    """Job events (the message stream) and uploaded files."""

    def __init__(self, db):
        self.db = db

    def add_event(self, job, time, kind, data):
        self.db.execute(
            "INSERT INTO events(job,time,type,data) VALUES(?,?,?,?)", (job, time, kind, data)
        )

    def all_events(self, job):
        return self.db.execute("SELECT * FROM events WHERE job=? ORDER BY id", (job,)).fetchall()

    def turn_edits(self, job):
        """A job's stored ``turn_edit`` payloads, in the order they were written."""
        rows = self.db.execute(
            "SELECT data FROM events WHERE job=? AND type='turn_edit' ORDER BY id", (job,)
        ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def ran_shell(self, job):
        return (
            self.db.execute(
                "SELECT 1 FROM events WHERE job=? AND type='tool_start' "
                "AND json_extract(data,'$.tool') IN ('commandExecution','Bash')",
                (job,),
            ).fetchone()
            is not None
        )

    def requests(self, job):
        return self.db.execute(
            "SELECT type,data FROM events WHERE job=? AND type IN ('approval_required','gate_required') ORDER BY id DESC",
            (job,),
        ).fetchall()

    def has_unattended_denial(self, job):
        """Whether an unattended run denied an action that needed approval (D15)."""
        return (
            self.db.execute(
                "SELECT 1 FROM events WHERE job=? AND type='approval_denied' "
                "AND json_extract(data,'$.scope')='unattended' LIMIT 1",
                (job,),
            ).fetchone()
            is not None
        )

    def last_event(self, job):
        return self.db.execute(
            "SELECT type,data FROM events WHERE job=? ORDER BY id DESC LIMIT 1", (job,)
        ).fetchone()

    def events_after(self, job, cursor):
        return self.db.execute(
            "SELECT * FROM events WHERE job=? AND id>? ORDER BY id LIMIT 200",
            (job, cursor),
        ).fetchall()

    def events_before(self, job, before=None, after=0, limit=200):
        return self.db.execute(
            "SELECT * FROM events WHERE job=? AND id>? AND (? IS NULL OR id<?) "
            "ORDER BY id DESC LIMIT ?",
            (job, after, before, before, limit),
        ).fetchall()

    def answer_deltas(self, job):
        """A job's streamed ``answer_delta`` payloads, in the order they were sent."""
        return self.db.execute(
            "SELECT data FROM events WHERE job=? AND type='answer_delta' ORDER BY id",
            (job,),
        ).fetchall()

    # CROSS JOIN pins the order: walk the caller's own jobs (owner, project index), then probe
    # each one's events through (job, type, time). Without it SQLite scans every tenant's
    # tool_start events in the window.
    # The job bound lets the walk stop at jobs old enough to have no event in the window.
    TOOL_USAGE = (
        "SELECT json_extract(e.data,'$.tool') AS tool, count(*) AS uses, max(e.time) AS last_used "
        "FROM jobs j CROSS JOIN events e ON e.job=j.id AND e.type='tool_start' AND e.time>=? "
        "WHERE j.owner=? AND j.project=? AND json_extract(j.payload,'$.backend')=? "
        "AND j.created>=? "
        "GROUP BY tool ORDER BY uses DESC, tool LIMIT ?"
    )
    RUN_MARGIN_SECONDS = 86400  # a run takes hours at most; a day is generous

    def tool_usage(self, owner, project, backend, since, limit):
        """Per tool name: how often a provider's runs started it in a project since ``since``."""
        params = (since, owner, project, backend, since - self.RUN_MARGIN_SECONDS, limit)
        return self.db.execute(self.TOOL_USAGE, params).fetchall()

    def file(self, file_id, project, owner):
        return self.db.execute(
            "SELECT * FROM files WHERE id=? AND project=? AND owner=?", (file_id, project, owner)
        ).fetchone()

    def owned_file(self, file_id, owner):
        return self.db.execute(
            "SELECT * FROM files WHERE id=? AND owner=?", (file_id, owner)
        ).fetchone()

    def project_bytes(self, project):
        """Uploaded bytes kept for a project, each identical upload (sha256) counted once."""
        return self.db.execute(
            "SELECT coalesce(sum(size),0) FROM "
            "(SELECT max(size) AS size FROM files WHERE project=? GROUP BY coalesce(hash,id))",
            (project,),
        ).fetchone()[0]

    def same_content(self, project, digest):
        """A kept upload of the project with this sha256, whose source can be shared."""
        return self.db.execute(
            "SELECT id FROM files WHERE project=? AND hash=? LIMIT 1", (project, digest)
        ).fetchone()

    def files_only_in(self, jobs):
        """Uploads the given turns attach that no other turn attaches."""
        marks = ",".join("?" for _ in jobs)
        return self.db.execute(
            "SELECT DISTINCT f.id,f.project FROM jobs j, json_each(j.payload,'$.file_ids') r "
            f"JOIN files f ON f.id=r.value WHERE j.id IN ({marks}) AND NOT EXISTS ("
            "SELECT 1 FROM jobs o, json_each(o.payload,'$.file_ids') s "
            f"WHERE s.value=f.id AND o.id NOT IN ({marks}))",
            [*jobs, *jobs],
        ).fetchall()

    def delete_files(self, file_ids):
        self.db.executemany("DELETE FROM files WHERE id=?", [(fid,) for fid in file_ids])

    def add_file(self, file_id, project, name, size, digest, pages, owner):
        self.db.execute(
            "INSERT INTO files(id,project,name,size,hash,pages,owner) VALUES(?,?,?,?,?,?,?)",
            (file_id, project, name, size, digest, pages, owner),
        )


class ProjectRepository:
    """Registered projects, deleted project folders and uploaded workspaces."""

    def __init__(self, db):
        self.db = db

    def registered(self):
        return {
            row["id"]: json.loads(row["spec"])
            for row in self.db.execute("SELECT * FROM registered_projects")
        }

    def register(self, project, spec):
        self.db.execute(
            "INSERT INTO registered_projects VALUES(?,?) ON CONFLICT(id) DO UPDATE SET spec=excluded.spec",
            (project, spec),
        )

    def deleted_folders(self):
        return {row["id"] for row in self.db.execute("SELECT id FROM deleted_project_folders")}

    def mark_folders_deleted(self, projects):
        self.db.executemany(
            "INSERT OR IGNORE INTO deleted_project_folders VALUES(?)",
            [(pid,) for pid in projects],
        )

    def workspace(self, workspace):
        return self.db.execute("SELECT * FROM workspaces WHERE id=?", (workspace,)).fetchone()

    def workspaces(self, owner):
        return self.db.execute(
            "SELECT id,project,name,created FROM workspaces WHERE owner=? ORDER BY created DESC",
            (owner,),
        ).fetchall()

    def add_workspace(self, workspace, project, owner, name, created, manifest):
        self.db.execute(
            "INSERT INTO workspaces VALUES(?,?,?,?,?,?)",
            (workspace, project, owner, name, created, manifest),
        )
