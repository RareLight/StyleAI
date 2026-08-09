# Lightroom Plug-in Component

`plugin/StyleAI.lrdevplugin` is the Lightroom SDK frontend. It owns menus,
dialogs, selection snapshots, preview export, catalog metadata and collections,
virtual copies, Develop application/readback, progress, and the final handoff
state for durable backend jobs.

The release manifest registers only these File → Plug-in Extras workflows:

1. Prepare Photos
2. Learn From My Edits
3. Apply My Style
4. Rate Selected AI Edits
5. Styles & Training
6. Find More Training Examples

The plug-in communicates only with `http://127.0.0.1:19819`. On startup it
resolves `<catalog folder>/styleai.db`, launches the packaged service or current
development source, and initializes the database marker. A running service
cannot switch to a different database path. Backup restores also require that
marker; it is not Lightroom's catalog UUID.

Long operations use selection snapshots, `WorkCoordinator` lanes, bounded
backend jobs, and consolidated catalog transactions. Lightroom shutdown does
no service I/O; it returns immediately while the backend handles idle exit and
interrupted-job recovery.

For user behavior, see the [Plug-in Guide](Plugin-Guide). For implementation
rules, see the [Developer Guide](Developer-Guide).
