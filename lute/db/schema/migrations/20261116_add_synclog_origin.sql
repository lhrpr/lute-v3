-- Where a synclog row came from.
--
-- NULL: a local edit, written by the capture triggers.
-- A device id: a change received from that device and applied here.
--
-- Only the hub (the hosted Lute that devices sync with) writes non-NULL
-- origins.  It has to log what it applies, or a change from the desktop
-- would never reach the iPad; and it has to remember who sent it, so that
-- the desktop isn't sent its own change back.  Devices never log applied
-- changes at all (the triggers are off while applying).
--
-- For these rows SlChangedAt is the sending device's time, not the hub's,
-- so last-write-wins compares like with like.

ALTER TABLE "synclog" ADD COLUMN "SlOrigin" TEXT NULL;
