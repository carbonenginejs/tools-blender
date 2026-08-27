"""Pure-Python section decompressors used by Granny 2 files.

The Oodle1 implementation is a translation of the Boost-licensed decoder used
by ``@carbonenginejs/format-gr2``.  The BitKnit2 implementation is an MIT-
licensed Python port of CarbonEngineJS's clean-room decoder, following its
published format specification.  See the repository notices for complete
provenance.
"""

from __future__ import annotations

import struct


OODLE1_BACKREF_SIZES = (128, 192, 256, 512)
OODLE1_PARAMETER_BYTES = 36
OODLE1_STREAM_PADDING = 8
OODLE1_OUTPUT_SLACK = 512

BITKNIT2_MAGIC = 0x75B1
_BITKNIT2_TOTAL = 0x8000
_BITKNIT2_QUANTUM_BYTES = 0x10000
_BITKNIT2_LOOKUP_SHIFT = 5
_BITKNIT2_ADAPT_INTERVAL = 1024


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


class _BitKnitFrequencyModel:
    """Adaptive 15-bit cumulative-frequency model for BitKnit2 symbols."""

    def __init__(self, symbol_count: int, min_probability_count: int):
        evenly_distributed = symbol_count - min_probability_count
        cumulative = [0] * (symbol_count + 1)
        for symbol in range(evenly_distributed):
            cumulative[symbol] = (
                (_BITKNIT2_TOTAL - min_probability_count)
                * symbol
                // evenly_distributed
            )
        for symbol in range(evenly_distributed, symbol_count + 1):
            cumulative[symbol] = _BITKNIT2_TOTAL - symbol_count + symbol

        self.symbol_count = symbol_count
        self.cumulative = cumulative
        self.accumulators = [1] * symbol_count
        self.tick = 0
        self.increment = (_BITKNIT2_TOTAL - symbol_count) // _BITKNIT2_ADAPT_INTERVAL
        self.last_increment = (
            1
            + _BITKNIT2_TOTAL
            - symbol_count
            - self.increment * _BITKNIT2_ADAPT_INTERVAL
        )
        self.lookup = [0] * 1024
        self._rebuild_lookup()

    def _rebuild_lookup(self) -> None:
        symbol = 0
        for bucket in range(1024):
            threshold = bucket << _BITKNIT2_LOOKUP_SHIFT
            while threshold >= self.cumulative[symbol + 1]:
                symbol += 1
            self.lookup[bucket] = symbol

    def observe(self, symbol: int) -> None:
        accumulators = self.accumulators
        accumulators[symbol] += self.increment
        self.tick = (self.tick + 1) & (_BITKNIT2_ADAPT_INTERVAL - 1)
        if self.tick:
            return

        accumulators[symbol] += self.last_increment
        running_total = 0
        for index in range(1, self.symbol_count + 1):
            running_total += accumulators[index - 1]
            current = self.cumulative[index]
            self.cumulative[index] = current + ((running_total - current) >> 1)
            accumulators[index - 1] = 1
        self._rebuild_lookup()


def decompress_bitknit2(
    data: bytes | bytearray | memoryview, expanded_size: int
) -> bytes:
    """Decompress a Granny section-format 4 BitKnit2 word stream."""

    source = memoryview(data)
    output = bytearray(expanded_size)
    if expanded_size == 0:
        return bytes(output)

    word_count = len(source) >> 1
    word_index = 0

    def next_word() -> int:
        nonlocal word_index
        if word_index >= word_count:
            raise ValueError("BitKnit2: source underflow")
        position = word_index << 1
        word_index += 1
        return source[position] | (source[position + 1] << 8)

    if next_word() != BITKNIT2_MAGIC:
        raise ValueError("BitKnit2: bad magic word")

    command_models = [_BitKnitFrequencyModel(300, 36) for _ in range(4)]
    cache_reference_models = [_BitKnitFrequencyModel(40, 0) for _ in range(4)]
    exponent_model = _BitKnitFrequencyModel(21, 0)

    recent_offsets = [1] * 8
    recent_order = 0x76543210
    delta_offset = 1
    state_a = 0
    state_b = 0

    def initialize_entropy_states() -> None:
        nonlocal state_a, state_b
        merged = next_word() * 0x10000 + next_word()
        split = merged & 15
        merged //= 16
        if merged < 0x10000:
            merged = merged * 0x10000 + next_word()
        state_a = merged // (1 << split)
        if state_a < 0x10000:
            state_a = state_a * 0x10000 + next_word()
        modulus = 1 << (16 + split)
        state_b = ((merged % 0x10000) * 0x10000 + next_word()) % modulus + modulus

    def pop_bits(bit_count: int) -> int:
        nonlocal state_a, state_b
        divisor = 1 << bit_count
        value = state_a % divisor
        state_a //= divisor
        if state_a < 0x10000:
            state_a = state_a * 0x10000 + next_word()
        state_a, state_b = state_b, state_a
        return value

    def pop_symbol(model: _BitKnitFrequencyModel) -> int:
        nonlocal state_a, state_b
        cumulative = model.cumulative
        code = state_a & (_BITKNIT2_TOTAL - 1)
        symbol = model.lookup[code >> _BITKNIT2_LOOKUP_SHIFT]
        while code >= cumulative[symbol + 1]:
            symbol += 1
        frequency = cumulative[symbol + 1] - cumulative[symbol]
        state_a = (
            (state_a // _BITKNIT2_TOTAL) * frequency
            + code
            - cumulative[symbol]
        )
        if state_a < 0x10000:
            state_a = state_a * 0x10000 + next_word()
        model.observe(symbol)
        state_a, state_b = state_b, state_a
        return symbol

    output_offset = 0
    while output_offset < expanded_size:
        quantum_end = min(
            expanded_size,
            output_offset
            - (output_offset % _BITKNIT2_QUANTUM_BYTES)
            + _BITKNIT2_QUANTUM_BYTES,
        )

        if word_index >= word_count:
            raise ValueError("BitKnit2: source underflow")
        peek_position = word_index << 1
        peek_word = source[peek_position] | (source[peek_position + 1] << 8)

        if peek_word == 0:
            word_index += 1
            remaining_words = word_count - word_index
            quantum_remaining = quantum_end - output_offset
            copy_length = min(remaining_words * 2, quantum_remaining)
            start = word_index << 1
            output[output_offset : output_offset + copy_length] = source[
                start : start + copy_length
            ]
            output_offset += copy_length
            word_index += copy_length >> 1
            continue

        initialize_entropy_states()

        if output_offset == 0:
            output[output_offset] = pop_bits(8)
            output_offset += 1

        while output_offset < quantum_end:
            phase = output_offset & 3
            command = pop_symbol(command_models[phase])
            if command < 256:
                output[output_offset] = (
                    command + output[output_offset - delta_offset]
                ) & 0xFF
                output_offset += 1
                continue

            if command < 288:
                copy_length = command - 254
            else:
                bit_count = command - 287
                copy_length = (1 << bit_count) + pop_bits(bit_count) + 32

            reference = pop_symbol(cache_reference_models[phase])
            if reference < 8:
                shift = reference * 4
                slot = (recent_order >> shift) & 15
                copy_offset = recent_offsets[slot]
                if reference == 7:
                    recent_order = ((recent_order << 4) | slot) & 0xFFFFFFFF
                elif reference > 0:
                    mask = (16 << shift) - 1
                    recent_order = (
                        (recent_order & (~mask & 0xFFFFFFFF))
                        | (((recent_order << 4) | slot) & mask)
                    ) & 0xFFFFFFFF
            else:
                bit_count = pop_symbol(exponent_model)
                extra = pop_bits(bit_count & 15)
                if bit_count >= 16:
                    extra = extra * 0x10000 + next_word()
                copy_offset = (
                    32 * (1 << bit_count)
                    + extra * 32
                    + reference
                    - 39
                )
                slot7 = (recent_order >> 28) & 15
                slot6 = (recent_order >> 24) & 15
                recent_offsets[slot7] = recent_offsets[slot6]
                recent_offsets[slot6] = copy_offset

            delta_offset = copy_offset
            source_offset = output_offset - copy_offset
            if source_offset < 0:
                raise ValueError("BitKnit2: match source before output start")
            if output_offset + copy_length > expanded_size:
                raise ValueError("BitKnit2: match exceeds output")
            for index in range(copy_length):
                output[output_offset + index] = output[source_offset + index]
            output_offset += copy_length

        if state_a != 0x10000 and state_b != 0x10000:
            raise ValueError("BitKnit2: corrupt quantum end state")

    return bytes(output)


__all__ = [
    "BITKNIT2_MAGIC",
    "decompress_bitknit2",
    "decompress_oodle1",
    "read_oodle1_parameters",
]
