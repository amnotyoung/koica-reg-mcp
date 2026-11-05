import unittest
from regulation_map.documents import document_sections
from regulation_map.build import ks


class DocumentSectionsTests(unittest.TestCase):
    def test_article_numbers_in_supplements_do_not_replace_main_articles(self):
        text = '# 규정\n제1조(목적) 본문\n부칙 <2026.01.01.>\n제1조(시행일) 시행한다.\n## [별표 1] 기준\n<table><tr><td>값</td></tr></table>\n'
        sections = document_sections(text)
        self.assertEqual(''.join(s['text'] for s in sections), text)
        self.assertEqual([s['kind'] for s in sections], ['main', 'main', 'supplementary', 'supplementary', 'attachment'])
        self.assertIn('본문', sections[1]['text'])
        self.assertIn('<table>', sections[-1]['text'])

    def test_every_corpus_document_is_preserved_losslessly(self):
        count = 0
        for path in ks.DATA_DIR.glob('*.md'):
            if '_' not in path.stem:
                continue
            text = path.read_text(encoding='utf-8')
            sections = document_sections(text)
            self.assertEqual(''.join(s['text'] for s in sections), text, path.name)
            self.assertEqual(len({s['id'] for s in sections}), len(sections))
            count += 1
        self.assertGreaterEqual(count, 149)


if __name__ == '__main__':
    unittest.main()
