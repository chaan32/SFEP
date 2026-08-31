package com.sfep.equipmentmonitor.replay;

import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.sfep.equipmentmonitor.bundle.SafeFile;
import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVParser;
import org.apache.commons.csv.CSVRecord;

import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.Reader;
import java.io.UncheckedIOException;
import java.nio.channels.Channels;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.Iterator;
import java.util.List;
import java.util.Objects;
import java.util.Optional;

/** Streams replay rows from an authenticated disk snapshot while retaining the original descriptor. */
public final class ReplayCursor implements ReplayUnitCursor {
    private final ReplaySource source;
    private final AuthenticatedReplaySnapshot snapshot;
    private final CSVParser parser;
    private final Iterator<CSVRecord> records;
    private final CountingInputStream consumedBytes;
    private final MessageDigest consumedDigest;

    private ReplayEvent lookahead;
    private boolean eofVerified;
    private boolean closed;
    private boolean resourcesClosed;
    private long rowCount;
    private String firstEventId;
    private String lastEventId;

    private ReplayCursor(
            ReplaySource source,
            AuthenticatedReplaySnapshot snapshot,
            CSVParser parser,
            CountingInputStream consumedBytes,
            MessageDigest consumedDigest) {
        this.source = source;
        this.snapshot = snapshot;
        this.parser = parser;
        this.records = parser.iterator();
        this.consumedBytes = consumedBytes;
        this.consumedDigest = consumedDigest;
    }

    public static ReplayCursor open(ReplaySource source) {
        Objects.requireNonNull(source, "source");
        SafeFile original = SafeFile.captureStandalone(source.path());
        AuthenticatedReplaySnapshot snapshot = null;
        CSVParser parser = null;
        try {
            snapshot = AuthenticatedReplaySnapshot.capture(
                    original,
                    source.sizeBytes(),
                    source.sha256(),
                    "REPLAY_DESCRIPTOR_SIZE_MISMATCH",
                    "REPLAY_DESCRIPTOR_HASH_MISMATCH");

            MessageDigest consumedDigest = sha256();
            InputStream input = Channels.newInputStream(snapshot.channel());
            DigestInputStream hashing = new DigestInputStream(input, consumedDigest);
            CountingInputStream counting = new CountingInputStream(hashing);
            var decoder = StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT);
            Reader reader = new InputStreamReader(counting, decoder);
            CSVFormat format = CSVFormat.RFC4180.builder()
                    .setHeader()
                    .setSkipHeaderRecord(true)
                    .get();
            parser = format.parse(reader);
            if (!parser.getHeaderNames().equals(ReplayCsvValidator.HEADER)) {
                throw new BundleLoadException(
                        "REPLAY_HEADER_INVALID",
                        "expected " + ReplayCsvValidator.HEADER + " but found " + parser.getHeaderNames());
            }
            return new ReplayCursor(source, snapshot, parser, counting, consumedDigest);
        } catch (BundleLoadException error) {
            closeQuietly(parser, snapshot, error);
            throw error;
        } catch (UncheckedIOException | IOException error) {
            BundleLoadException failure = hasCharacterCodingCause(error)
                    ? new BundleLoadException("REPLAY_UTF8_INVALID", source.path().toString(), error)
                    : new BundleLoadException("REPLAY_CSV_INVALID", source.path().toString(), error);
            closeQuietly(parser, snapshot, failure);
            throw failure;
        } catch (RuntimeException error) {
            closeQuietly(parser, snapshot, error);
            throw error;
        }
    }

    @Override
    public Optional<ReplayUnit> nextUnit() {
        ensureOpen();
        ReplayEvent first = lookahead;
        lookahead = null;
        if (first == null) {
            first = nextEvent();
        }
        if (first == null) {
            return Optional.empty();
        }

        List<ReplayEvent> events = new ArrayList<>();
        events.add(first);
        while (true) {
            ReplayEvent event = nextEvent();
            if (event == null) {
                break;
            }
            if (event.batchId().equals(first.batchId())
                    && event.batchStep().equals(first.batchStep())) {
                events.add(event);
            } else {
                lookahead = event;
                break;
            }
        }
        return Optional.of(ReplayUnit.from(events));
    }

    private ReplayEvent nextEvent() {
        if (eofVerified) {
            return null;
        }
        try {
            if (!records.hasNext()) {
                verifyConsumedSnapshot();
                eofVerified = true;
                return null;
            }
            CSVRecord record = records.next();
            if (record.size() != ReplayCsvValidator.HEADER.size()) {
                throw new BundleLoadException(
                        "REPLAY_CSV_INVALID",
                        "record " + record.getRecordNumber() + " has " + record.size() + " columns");
            }
            ReplayEvent event = ReplayCsvValidator.bind(ReplayCsvValidator.rowNode(record));
            if (!source.bundleId().equals(event.bundleId())) {
                throw new BundleLoadException("REPLAY_BUNDLE_ID_MISMATCH", event.eventId());
            }
            if (!source.criteriaId().equals(event.criteriaId())) {
                throw new BundleLoadException("REPLAY_CRITERIA_ID_MISMATCH", event.eventId());
            }
            if (firstEventId == null) {
                firstEventId = event.eventId();
            }
            lastEventId = event.eventId();
            rowCount++;
            return event;
        } catch (BundleLoadException error) {
            closeAfterFailure(error);
            throw error;
        } catch (UncheckedIOException error) {
            BundleLoadException failure = hasCharacterCodingCause(error)
                    ? new BundleLoadException("REPLAY_UTF8_INVALID", source.path().toString(), error)
                    : new BundleLoadException("REPLAY_CSV_INVALID", source.path().toString(), error);
            closeAfterFailure(failure);
            throw failure;
        } catch (RuntimeException error) {
            RuntimeException failure = hasCharacterCodingCause(error)
                    ? new BundleLoadException("REPLAY_UTF8_INVALID", source.path().toString(), error)
                    : new BundleLoadException("REPLAY_CSV_INVALID", source.path().toString(), error);
            closeAfterFailure(failure);
            throw failure;
        }
    }

    private void verifyConsumedSnapshot() {
        if (consumedBytes.count() != source.sizeBytes()) {
            throw new BundleLoadException(
                    "REPLAY_CONSUMED_SIZE_MISMATCH",
                    "expected " + source.sizeBytes() + " but consumed " + consumedBytes.count());
        }
        String actualSha256 = digestUri(consumedDigest);
        if (!actualSha256.equals(source.sha256())) {
            throw new BundleLoadException(
                    "REPLAY_CONSUMED_HASH_MISMATCH",
                    "expected " + source.sha256() + " but consumed " + actualSha256);
        }
        snapshot.verifyOriginal(source.sizeBytes(), source.sha256());
        ReplayMetadata expected = source.metadata();
        if (expected == null
                || rowCount != expected.rowCount()
                || !Objects.equals(firstEventId, expected.firstEventId())
                || !Objects.equals(lastEventId, expected.lastEventId())) {
            throw new BundleLoadException(
                    "REPLAY_METADATA_MISMATCH",
                    "consumed rows/events differ from validated replay metadata");
        }
        snapshot.assertOriginalUnchanged();
        closeResources();
    }

    private void ensureOpen() {
        if (closed) {
            throw new IllegalStateException("replay cursor is closed");
        }
    }

    @Override
    public boolean exhausted() {
        return eofVerified && lookahead == null;
    }

    @Override
    public void close() {
        if (closed) {
            return;
        }
        closed = true;
        closeResources();
    }

    private void closeResources() {
        if (resourcesClosed) return;
        resourcesClosed = true;
        BundleLoadException failure = null;
        try {
            parser.close();
        } catch (IOException error) {
            failure = new BundleLoadException("REPLAY_CLOSE_FAILED", source.path().toString(), error);
        }
        try {
            snapshot.close();
        } catch (RuntimeException error) {
            if (failure == null) throw error;
            failure.addSuppressed(error);
        }
        if (failure != null) throw failure;
    }

    private void closeAfterFailure(Throwable failure) {
        try {
            closeResources();
        } catch (RuntimeException cleanupError) {
            failure.addSuppressed(cleanupError);
        }
    }

    private static void closeQuietly(
            CSVParser parser,
            AuthenticatedReplaySnapshot snapshot,
            Throwable failure) {
        try {
            if (parser != null) {
                parser.close();
            }
        } catch (IOException cleanupError) {
            failure.addSuppressed(cleanupError);
        }
        if (snapshot != null) {
            try {
                snapshot.close();
            } catch (RuntimeException cleanupError) {
                failure.addSuppressed(cleanupError);
            }
        }
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 unavailable", impossible);
        }
    }

    private static String digestUri(MessageDigest digest) {
        return "sha256:" + HexFormat.of().formatHex(digest.digest());
    }

    private static boolean hasCharacterCodingCause(Throwable error) {
        for (Throwable current = error; current != null; current = current.getCause()) {
            if (current instanceof CharacterCodingException) {
                return true;
            }
        }
        return false;
    }

    private static final class CountingInputStream extends FilterInputStream {
        private long count;

        private CountingInputStream(InputStream input) {
            super(input);
        }

        @Override
        public int read() throws IOException {
            int value = super.read();
            if (value >= 0) {
                count++;
            }
            return value;
        }

        @Override
        public int read(byte[] bytes, int offset, int length) throws IOException {
            int consumed = super.read(bytes, offset, length);
            if (consumed > 0) {
                count += consumed;
            }
            return consumed;
        }

        private long count() {
            return count;
        }
    }
}
