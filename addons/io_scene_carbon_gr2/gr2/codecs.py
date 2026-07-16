"""Pure-Python section decompressors used by Granny 2 files.

The Oodle1 implementation is a translation of the Boost-licensed decoder used
by ``@carbonenginejs/format-gr2``.  The BitKnit2 implementation is translated
from that package's EUPL-1.2 implementation.  See the repository notices for
complete provenance.
"""

from __future__ import annotations

import struct


OODLE1_BACKREF_SIZES = (128, 192, 256, 512)
OODLE1_PARAMETER_BYTES = 36
OODLE1_STREAM_PADDING = 8
OODLE1_OUTPUT_SLACK = 512

BITKNIT2_MAGIC = 0x75B1
BITKNIT2_FREQ_BITS = 15
BITKNIT2_LOOKUP_BITS = 10
BITKNIT2_LOOKUP_SHIFT = BITKNIT2_FREQ_BITS - BITKNIT2_LOOKUP_BITS
BITKNIT2_TOTAL_SUM = 1 << BITKNIT2_FREQ_BITS
BITKNIT2_ADAPT_INTERVAL = 1024


class _OodleDecoder:
    def __init__(self, stream: bytes | bytearray | memoryview):
        self.stream = stream
        self.pos = 0
        self.numer = stream[0] >> 1
        self.denom = 0x80
        self.next_denom = 0

    def decode(self, maximum: int) -> int:
        while self.denom <= 0x800000:
            self.denom = (self.denom << 8) & 0xFFFFFFFF
            self.numer = (
                (self.numer << 8)
                | ((self.stream[self.pos] << 7) & 0x80)
                | ((self.stream[self.pos + 1] >> 1) & 0x7F)
            ) & 0xFFFFFFFF
            self.pos += 1
        self.next_denom = self.denom // maximum
        return min(self.numer // self.next_denom, maximum - 1)

    def commit(self, maximum: int, value: int, width: int) -> int:
        self.numer -= self.next_denom * value
        if value + width < maximum:
            self.denom = self.next_denom * width
        else:
            self.denom -= self.next_denom * value
        return value

    def decode_commit(self, maximum: int) -> int:
        return self.commit(maximum, self.decode(maximum), 1)


class _WeighWindow:
    def __init__(self, max_value: int, count_cap: int):
        self.weight_total = 4
        self.count_cap = (count_cap + 1) & 0xFFFF
        self.ranges = [0, 0x4000]
        self.weights = [4]
        self.values = [0]
        self.thresh_increase = 4
        self.thresh_range_rebuild = 8
        self.thresh_weight_rebuild = max(256, min(32 * max_value, 15160))
        self.thresh_increase_cap = (
            min(2 * max_value, (self.thresh_weight_rebuild >> 1) - 32)
            if max_value > 64
            else 128
        )

    def rebuild_ranges(self) -> None:
        self.ranges = [0] * (len(self.weights) + 1)
        range_weight = (8 * 0x4000) // self.weight_total
        start = 0
        for index, weight in enumerate(self.weights):
            self.ranges[index] = start
            start += (weight * range_weight) // 8
        self.ranges[len(self.weights)] = 0x4000

        if self.thresh_increase > (self.thresh_increase_cap >> 1):
            self.thresh_range_rebuild = self.weight_total + self.thresh_increase_cap
        else:
            self.thresh_increase *= 2
            self.thresh_range_rebuild = self.weight_total + self.thresh_increase

    def rebuild_weights(self) -> None:
        total = 0
        for index in range(len(self.weights)):
            self.weights[index] >>= 1
            total += self.weights[index]
        self.weight_total = total

        index = 1
        while index < len(self.weights):
            if self.weights[index] == 0:
                self.weights[index] = self.weights[-1]
                self.weights.pop()
                self.values[index] = self.values[-1]
                self.values.pop()
            else:
                index += 1

        if len(self.weights) > 1:
            max_index = max(range(1, len(self.weights)), key=self.weights.__getitem__)
            last = len(self.weights) - 1
            self.weights[max_index], self.weights[last] = self.weights[last], self.weights[max_index]
            self.values[max_index], self.values[last] = self.values[last], self.values[max_index]

        if len(self.weights) < self.count_cap and self.weights[0] == 0:
            self.weights[0] = 1
            self.weight_total += 1

    def try_decode(self, decoder: _OodleDecoder) -> tuple[int, int]:
        if self.weight_total >= self.thresh_range_rebuild:
            if self.thresh_range_rebuild >= self.thresh_weight_rebuild:
                self.rebuild_weights()
            self.rebuild_ranges()

        value = decoder.decode(0x4000)
        low = 0
        high = len(self.ranges)
        while low < high:
            middle = (low + high) >> 1
            if self.ranges[middle] <= value:
                low = middle + 1
            else:
                high = middle
        index = low - 1
        decoder.commit(
            0x4000,
            self.ranges[index],
            self.ranges[index + 1] - self.ranges[index],
        )

        self.weights[index] += 1
        self.weight_total += 1
        if index > 0:
            return -1, self.values[index]

        if len(self.weights) >= len(self.ranges) and decoder.decode_commit(2) == 1:
            reuse = len(self.ranges) + decoder.decode_commit(
                len(self.weights) - len(self.ranges) + 1
            ) - 1
            self.weights[reuse] += 2
            self.weight_total += 2
            return -1, self.values[reuse]

        self.values.append(0)
        self.weights.append(2)
        self.weight_total += 2
        if len(self.weights) == self.count_cap:
            self.weight_total -= self.weights[0]
            self.weights[0] = 0
        return len(self.values) - 1, 0

    def store_value(self, index: int, value: int) -> int:
        self.values[index] = value
        return value


class _OodleDictionary:
    def __init__(self, params: dict[str, object]):
        self.decoded_size = 0
        self.backref_size = 0
        self.decoded_value_max = int(params["decoded_value_max"])
        self.backref_value_max = int(params["backref_value_max"])
        self.lowbit_value_max = min(self.backref_value_max + 1, 4)
        self.midbit_value_max = min(self.backref_value_max // 4 + 1, 256)
        self.highbit_value_max = self.backref_value_max // 1024 + 1

        self.lowbit_window = _WeighWindow(self.lowbit_value_max - 1, self.lowbit_value_max)
        self.highbit_window = _WeighWindow(
            self.highbit_value_max - 1, int(params["highbit_count"]) + 1
        )
        self.midbit_windows = [
            _WeighWindow(self.midbit_value_max - 1, self.midbit_value_max)
            for _ in range(self.highbit_value_max)
        ]
        self.decoded_windows = [
            _WeighWindow(self.decoded_value_max - 1, int(params["decoded_count"]))
            for _ in range(4)
        ]
        sizes_count = params["sizes_count"]
        self.size_windows = []
        for outer in range(4):
            for _ in range(16):
                self.size_windows.append(_WeighWindow(64, sizes_count[3 - outer]))
        self.size_windows.append(_WeighWindow(64, sizes_count[0]))

    @staticmethod
    def _decode_window(
        window: _WeighWindow, decoder: _OodleDecoder, maximum: int
    ) -> int:
        new_index, value = window.try_decode(decoder)
        if new_index >= 0:
            value = window.store_value(new_index, decoder.decode_commit(maximum))
        return value

    def decompress_block(
        self, decoder: _OodleDecoder, output: bytearray, position: int
    ) -> int:
        size_window = self.size_windows[self.backref_size]
        self.backref_size = self._decode_window(size_window, decoder, 65)

        if self.backref_size > 0:
            size = (
                self.backref_size + 1
                if self.backref_size < 61
                else OODLE1_BACKREF_SIZES[self.backref_size - 61]
            )
            distance_range = min(self.backref_value_max, self.decoded_size)
            low = self._decode_window(
                self.lowbit_window, decoder, self.lowbit_value_max
            )
            high = self._decode_window(
                self.highbit_window, decoder, distance_range // 1024 + 1
            )
            middle = self._decode_window(
                self.midbit_windows[high],
                decoder,
                min(distance_range // 4 + 1, 256),
            )
            offset = (high << 10) + (middle << 2) + low + 1
            self.decoded_size += size
            source = position - offset
            for index in range(size):
                output[position + index] = output[source + index]
            return size

        literal_window = self.decoded_windows[position & 3]
        value = self._decode_window(
            literal_window, decoder, self.decoded_value_max
        )
        output[position] = value & 0xFF
        self.decoded_size += 1
        return 1


def read_oodle1_parameters(data: bytes | bytearray | memoryview) -> list[dict[str, object]]:
    view = memoryview(data)
    params = []
    for index in range(3):
        base = index * 12
        first, second = struct.unpack_from("<II", view, base)
        params.append(
            {
                "decoded_value_max": first & 0x1FF,
                "backref_value_max": first >> 9,
                "decoded_count": second & 0x1FF,
                "highbit_count": (second >> 19) & 0x1FFF,
                "sizes_count": list(view[base + 8 : base + 12]),
            }
        )
    return params


def decompress_oodle1(
    data: bytes | bytearray | memoryview,
    expanded_size: int,
    *,
    first16: int,
    first8: int,
) -> bytes:
    view = memoryview(data)
    output = bytearray(expanded_size + OODLE1_OUTPUT_SLACK)
    if not view or expanded_size == 0:
        return bytes(output[:expanded_size])
    if len(view) < OODLE1_PARAMETER_BYTES:
        raise ValueError("Oodle1 block too small for parameter header")

    params = read_oodle1_parameters(view)
    stream = bytearray(len(view) - OODLE1_PARAMETER_BYTES + OODLE1_STREAM_PADDING)
    stream[: len(view) - OODLE1_PARAMETER_BYTES] = view[OODLE1_PARAMETER_BYTES:]
    decoder = _OodleDecoder(stream)

    position = 0
    for stop, block_params in zip((first16, first8, expanded_size), params):
        dictionary = _OodleDictionary(block_params)
        while position < stop:
            position += dictionary.decompress_block(decoder, output, position)
    return bytes(output[:expanded_size])


class _BitKnitModel:
    def __init__(self, vocab_size: int, minimum_probable: int):
        equi = vocab_size - minimum_probable
        self.vocab = vocab_size
        self.freq_incr = (BITKNIT2_TOTAL_SUM - vocab_size) // BITKNIT2_ADAPT_INTERVAL
        self.last_freq_incr = (
            1
            + BITKNIT2_TOTAL_SUM
            - vocab_size
            - self.freq_incr * BITKNIT2_ADAPT_INTERVAL
        )
        self.sums = [0] * (vocab_size + 1)
        self.lookup = [0] * (1 << BITKNIT2_LOOKUP_BITS)
        self.acc = [1] * vocab_size
        self.counter = 0
        for index in range(equi):
            self.sums[index] = (
                (BITKNIT2_TOTAL_SUM - minimum_probable) * index // equi
            )
        for index in range(equi, vocab_size + 1):
            self.sums[index] = BITKNIT2_TOTAL_SUM - vocab_size + index
        self.finish_update()

    def finish_update(self) -> None:
        code = 0
        symbol = 0
        next_sum = self.sums[1]
        while code < BITKNIT2_TOTAL_SUM:
            if code < next_sum:
                self.lookup[code >> BITKNIT2_LOOKUP_SHIFT] = symbol
                code += 1 << BITKNIT2_LOOKUP_SHIFT
            else:
                symbol += 1
                next_sum = self.sums[symbol + 1]

    def observe(self, symbol: int) -> None:
        self.acc[symbol] = (self.acc[symbol] + self.freq_incr) & 0xFFFF
        self.counter = (self.counter + 1) & (BITKNIT2_ADAPT_INTERVAL - 1)
        if self.counter != 0:
            return

        self.acc[symbol] = (self.acc[symbol] + self.last_freq_incr) & 0xFFFF
        total = 0
        for index in range(1, self.vocab + 1):
            total += self.acc[index - 1]
            self.sums[index] = (
                self.sums[index] + ((total - self.sums[index]) >> 1)
            ) & 0xFFFF
            self.acc[index - 1] = 1
        self.finish_update()


def decompress_bitknit2(
    data: bytes | bytearray | memoryview, expanded_size: int
) -> bytes:
    source = memoryview(data)
    destination = bytearray(expanded_size)
    if expanded_size == 0:
        return bytes(destination)

    word_count = len(source) >> 1
    word_index = 0

    def word() -> int:
        nonlocal word_index
        if word_index >= word_count:
            raise ValueError("BitKnit2: source underflow")
        value = struct.unpack_from("<H", source, word_index << 1)[0]
        word_index += 1
        return value

    def peek() -> int:
        if word_index >= word_count:
            raise ValueError("BitKnit2: source underflow")
        return struct.unpack_from("<H", source, word_index << 1)[0]

    if word() != BITKNIT2_MAGIC:
        raise ValueError("BitKnit2: bad magic")

    command_models = [_BitKnitModel(300, 36) for _ in range(4)]
    cache_models = [_BitKnitModel(40, 0) for _ in range(4)]
    copy_offset_model = _BitKnitModel(21, 0)
    lru_entries = [1] * 8
    lru_order = 0x76543210

    def lru_insert(value: int) -> None:
        lru_entries[lru_order >> 28] = lru_entries[(lru_order >> 24) & 15]
        lru_entries[(lru_order >> 24) & 15] = value

    def lru_hit(index: int) -> int:
        nonlocal lru_order
        slot = (lru_order >> (index * 4)) & 15
        rotate_mask = 0xFFFFFFFF if index == 7 else (16 << (index * 4)) - 1
        rotated = ((lru_order * 16 + slot) & rotate_mask) & 0xFFFFFFFF
        lru_order = ((lru_order & (~rotate_mask & 0xFFFFFFFF)) | rotated) & 0xFFFFFFFF
        return lru_entries[slot]

    bits1 = 0x10000
    bits2 = 0x10000
    delta_offset = 1
    output_offset = 0

    def refill1() -> None:
        nonlocal bits1
        if bits1 < 0x10000:
            bits1 = bits1 * 65536 + word()

    def pop_bits(bit_count: int) -> int:
        nonlocal bits1, bits2
        symbol = bits1 & ((1 << bit_count) - 1)
        bits1 //= 1 << bit_count
        refill1()
        bits1, bits2 = bits2, bits1
        return symbol

    def pop_model(model: _BitKnitModel) -> int:
        nonlocal bits1, bits2
        code = bits1 & (BITKNIT2_TOTAL_SUM - 1)
        symbol = model.lookup[code >> BITKNIT2_LOOKUP_SHIFT]
        while code >= model.sums[symbol + 1]:
            symbol += 1
        frequency = model.sums[symbol + 1] - model.sums[symbol]
        bits1 = (bits1 >> BITKNIT2_FREQ_BITS) * frequency + code - model.sums[symbol]
        refill1()
        model.observe(symbol)
        bits1, bits2 = bits2, bits1
        return symbol

    while output_offset < expanded_size:
        boundary = min(expanded_size, (output_offset & ~0xFFFF) + 0x10000)
        if peek() == 0:
            word_index += 1
            copy_length = min((word_count - word_index) * 2, boundary - output_offset)
            start = word_index << 1
            destination[output_offset : output_offset + copy_length] = source[
                start : start + copy_length
            ]
            output_offset += copy_length
            word_index += copy_length >> 1
            continue

        merged = word() * 65536 + word()
        split = merged & 15
        merged //= 16
        if merged < 0x10000:
            merged = merged * 65536 + word()
        bits1 = merged >> split
        if bits1 < 0x10000:
            bits1 = bits1 * 65536 + word()
        modulus = 2 ** (16 + split)
        bits2 = ((merged % 65536) * 65536 + word()) % modulus + modulus

        if output_offset == 0:
            destination[output_offset] = pop_bits(8)
            output_offset += 1

        while output_offset < boundary:
            phase = output_offset & 3
            command = pop_model(command_models[phase])
            if command < 256:
                destination[output_offset] = (
                    command + destination[output_offset - delta_offset]
                ) & 0xFF
                output_offset += 1
                continue

            if command < 288:
                copy_length = command - 254
            else:
                bit_count = command - 287
                copy_length = (1 << bit_count) + pop_bits(bit_count) + 32

            cache_reference = pop_model(cache_models[phase])
            if cache_reference < 8:
                copy_offset = lru_hit(cache_reference)
            else:
                bit_count = pop_model(copy_offset_model)
                extra = pop_bits(bit_count & 15)
                if bit_count >= 16:
                    extra = extra * 65536 + word()
                copy_offset = (
                    (32 * 2**bit_count if bit_count >= 27 else 32 << bit_count)
                    + extra * 32
                    + cache_reference
                    - 39
                )
                lru_insert(copy_offset)

            delta_offset = copy_offset
            source_offset = output_offset - copy_offset
            if source_offset < 0:
                raise ValueError("BitKnit2: match before start")
            for _ in range(copy_length):
                if output_offset >= expanded_size:
                    raise ValueError("BitKnit2: match exceeds output")
                destination[output_offset] = destination[source_offset]
                output_offset += 1
                source_offset += 1

        if bits1 != 0x10000 and bits2 != 0x10000:
            raise ValueError("BitKnit2: rANS stream corrupted")

    return bytes(destination)


__all__ = [
    "BITKNIT2_MAGIC",
    "decompress_bitknit2",
    "decompress_oodle1",
    "read_oodle1_parameters",
]
