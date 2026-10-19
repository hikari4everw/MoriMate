"""Deterministic Chinese/Latin lexical retrieval, after scope filtering."""
import json
import re


def terms(text):
    result = set(re.findall(r'[a-z0-9_]+', text.casefold()))
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        result.update(run[i:i + 2] for i in range(len(run) - 1))
        if len(run) == 1:
            result.add(run)
    return result


class MemoryRetriever:
    def __init__(self, store):
        self.store = store

    def search(self, query, scene, limit=12):
        wanted = terms(query)
        ranked = []
        for memory in self.store.memories():
            if memory['scene'] not in {None, scene}:
                continue
            ids = json.loads(memory['source_ids'])
            if ids and not self.store._valid_sources(ids):
                continue
            score = len(wanted.intersection(terms(memory['text'])))
            # With no query (preview/debug), show scoped current facts.
            if score or not query:
                ranked.append({**memory, 'score': score, 'reason': '词项匹配' if score else '当前场景预览'})
        ranked.sort(key=lambda item: (item['score'], item['id']), reverse=True)
        return ranked[:limit]
