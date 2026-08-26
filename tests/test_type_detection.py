"""type_detection.detect() is a pure function -- no Qt, no DB -- so it's
fully unit-testable. Emphasis on the negative cases (things that must NOT
be misclassified) as much as the positive ones, per the 'no clasifiques
agresivamente si la certeza es baja' requirement."""

from nexus_linux.type_detection import detect


class TestStructuralKinds:
    def test_image_kind_passes_through(self):
        assert detect("image", None) == "image"

    def test_file_kind_passes_through(self):
        assert detect("file", "/home/user/doc.pdf") == "file"

    def test_empty_text_is_plain_text(self):
        assert detect("text", "") == "text"
        assert detect("text", "   ") == "text"
        assert detect("text", None) == "text"


class TestURLAndYouTube:
    def test_url_kind_is_url(self):
        assert detect("url", "https://example.com/path") == "url"

    def test_youtube_watch_url_detected(self):
        assert detect("url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"

    def test_youtube_short_url_detected(self):
        assert detect("url", "https://youtu.be/dQw4w9WgXcQ") == "youtube"

    def test_youtube_shorts_detected(self):
        assert detect("url", "https://youtube.com/shorts/abc123") == "youtube"

    def test_plain_text_url_still_detected_as_url(self):
        """A URL typed/copied as plain text (kind=text, not via uri-list)
        must still get the URL card, not a generic text card."""
        assert detect("text", "https://example.com") == "url"

    def test_www_without_scheme_detected(self):
        assert detect("text", "www.example.com/page") == "url"


class TestColor:
    def test_hex_3_digit(self):
        assert detect("text", "#fff") == "color"

    def test_hex_6_digit(self):
        assert detect("text", "#FF00AA") == "color"

    def test_hex_8_digit_with_alpha(self):
        assert detect("text", "#FF00AACC") == "color"

    def test_rgb_function(self):
        assert detect("text", "rgb(255, 0, 170)") == "color"

    def test_rgba_function(self):
        assert detect("text", "rgba(255, 0, 170, 0.5)") == "color"

    def test_hsl_function(self):
        assert detect("text", "hsl(320, 100%, 50%)") == "color"

    def test_hex_like_word_is_not_a_color(self):
        # Looks hex-ish but has non-hex chars and no leading #.
        assert detect("text", "ffgggg") != "color"


class TestEmailAndPhone:
    def test_email_detected(self):
        assert detect("text", "manuel@example.com") == "email"

    def test_email_with_plus_tag_detected(self):
        assert detect("text", "manuel+nexus@example.com") == "email"

    def test_phone_number_detected(self):
        assert detect("text", "+52 55 1234 5678") == "phone"

    def test_short_digit_string_is_not_a_phone(self):
        # Too short to confidently be a phone number.
        assert detect("text", "123") != "phone"

    def test_word_is_not_an_email(self):
        assert detect("text", "not an email at all") == "text"


class TestPathAndCommand:
    def test_absolute_path_detected(self):
        assert detect("text", "/home/manuelcarvajal/Projects/Nexus-linux") == "path"

    def test_home_relative_path_detected(self):
        assert detect("text", "~/Documents/report.pdf") == "path"

    def test_path_with_spaces_is_not_classified_as_path(self):
        assert detect("text", "/usr/bin/env is a program") != "path"

    def test_git_command_detected(self):
        assert detect("text", "git commit -m 'fix bug'") == "command"

    def test_docker_command_detected(self):
        assert detect("text", "docker run -it ubuntu bash") == "command"

    def test_ambiguous_english_word_prefix_needs_a_second_signal(self):
        # "go", "make", "cat", "export" are also ordinary English words --
        # must NOT become a command card without a corroborating flag/path.
        assert detect("text", "go to the store and buy milk") == "text"
        assert detect("text", "make sure to email me tomorrow") == "text"
        assert detect("text", "cat is sleeping on the couch") == "text"

    def test_ambiguous_prefix_with_a_flag_is_a_command(self):
        assert detect("text", "make -j4 build") == "command"
        assert detect("text", "cat -n file.txt") == "command"


class TestJSON:
    def test_json_object_detected(self):
        assert detect("text", '{"name": "nexus", "version": 2}') == "json"

    def test_json_array_detected(self):
        assert detect("text", '[1, 2, 3, "four"]') == "json"

    def test_malformed_json_is_not_classified_as_json(self):
        assert detect("text", '{name: nexus, broken}') != "json"

    def test_bare_number_is_not_json_even_though_it_parses(self):
        # json.loads("42") succeeds and returns an int -- must not count.
        assert detect("text", "42") != "json"

    def test_curly_brace_in_prose_is_not_json(self):
        assert detect("text", "{this is just a note in braces}") != "json"


class TestCode:
    def test_python_function_detected(self):
        code = "def add(a, b):\n    return a + b\n"
        assert detect("text", code) == "code"

    def test_javascript_arrow_function_detected(self):
        code = "const add = (a, b) => {\n  return a + b;\n};"
        assert detect("text", code) == "code"

    def test_single_line_with_braces_is_not_code(self):
        # Single-line heuristics only apply to color/email/phone/path/command;
        # a lone brace-containing sentence must not become a code card.
        assert detect("text", "remember to check the {status} field") == "text"

    def test_two_line_plain_prose_is_not_code(self):
        prose = "Dear team,\nThanks for the update yesterday."
        assert detect("text", prose) == "text"

    def test_indented_multiline_block_detected_as_code(self):
        code = "if (x) {\n    doSomething();\n    doMore();\n}"
        assert detect("text", code) == "code"


class TestPriorityOrdering:
    def test_url_wins_over_generic_text_even_with_multiple_lines_no(self):
        # Multi-line content is never URL-classified (URLs are single-line by
        # construction here) -- confirms multi-line doesn't accidentally
        # trigger the URL branch.
        assert detect("text", "https://example.com\nsecond line") == "text"

    def test_kind_url_always_wins_regardless_of_content_shape(self):
        assert detect("url", "not even a real url shape") == "url"
