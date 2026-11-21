"""Vanilla Dialogues: how heard lines read for the AI. Always in third person with the NPCs' names, like the summaries,
so they stay clear in a group prompt that holds several NPCs' memories"""
from abc import ABC, abstractmethod
from src.heard_lines.heard_line import GameTime, HeardLine, HeardPerson


class HeardLinesText(ABC):
    @abstractmethod
    def memory_entries(self, lines: list[HeardLine], listener: HeardPerson, now: GameTime | None, hide_player: bool) -> list[tuple[int, str]]:
        """The (real time, text) entries the lines add to the listener's memory: one per scene, or per menu talk with one NPC.

        Args:
            now (GameTime | None): the game's time now, for "Earlier today", "Yesterday", "N days ago". None leaves them out
            hide_player (bool): Director Mode: leaves out menu talk and that the player was there
        """
        pass

    @abstractmethod
    def nearby_now(self, line: HeardLine, hide_player: bool) -> str:
        """One narration sentence for a line heard during a conversation, such as Nearby, Mikael says to Carlotta: "...".
        Empty when it is left out (menu talk while hide_player)"""
        pass


class PlainHeardLinesText(HeardLinesText):
    def memory_entries(self, lines: list[HeardLine], listener: HeardPerson, now: GameTime | None, hide_player: bool) -> list[tuple[int, str]]:
        entries: list[tuple[int, str]] = []
        for group in self.__groups(sorted(lines, key=lambda l: l.time)):
            if group[0].is_menu_talk and hide_player:
                continue
            text = self.__menu_talk(group, listener) if group[0].is_menu_talk else self.__scene(group, listener, hide_player)
            entries.append((group[0].time, f"{self.__when_and_where(group[0], now)}{text}"))
        return entries

    def nearby_now(self, line: HeardLine, hide_player: bool) -> str:
        if line.is_menu_talk:
            if hide_player:
                return ""
            if line.asked:
                return f'Nearby, {line.player} asks {line.speaker.name}: "{line.asked}" {line.speaker.name} answers: "{line.words}"'
            return f'Nearby, {line.speaker.name} says to {line.player}: "{line.words}"'
        to = self.__to(line, hide_player)
        return f'Nearby, {line.speaker.name} says{f" to {to.name}" if to else ""}: "{line.words}"'

    @staticmethod
    def __groups(lines: list[HeardLine]) -> list[list[HeardLine]]:
        """Lines in a row from the same scene on the same day, or the same menu talk with one NPC"""
        groups: list[list[HeardLine]] = []
        for line in lines:
            key = PlainHeardLinesText.__group_key(line)
            if groups and PlainHeardLinesText.__group_key(groups[-1][-1]) == key:
                groups[-1].append(line)
            else:
                groups.append([line])
        return groups

    @staticmethod
    def __group_key(line: HeardLine) -> tuple:
        who = line.speaker.ref_id if line.is_menu_talk else ""
        return (line.scene, who, line.game_time.calendar_day)

    def __scene(self, group: list[HeardLine], listener: HeardPerson, hide_player: bool) -> str:
        sentences: list[str] = []
        previous: HeardLine | None = None
        for line in group:
            to = self.__to(line, hide_player)
            if previous is None:
                took_part = line.speaker.ref_id == listener.ref_id or (to is not None and to.ref_id == listener.ref_id)
                if took_part:
                    sentences.append(f'{line.speaker.name} said{f" to {to.name}" if to else ""}: "{line.words}"')
                else:
                    sentences.append(f'{listener.name} overheard {line.speaker.name} say{f" to {to.name}" if to else ""}: "{line.words}"')
            elif line.speaker.ref_id == previous.speaker.ref_id:
                sentences.append(f'{line.speaker.name} went on: "{line.words}"')
            elif previous.to is not None and previous.to.ref_id == line.speaker.ref_id:
                sentences.append(f'{line.speaker.name} answered: "{line.words}"')
            else:
                sentences.append(f'{line.speaker.name} said{f" to {to.name}" if to else ""}: "{line.words}"')
            previous = line
        player = group[0].player
        if not hide_player and player and any(line.player_there for line in group):
            sentences.append(f"{player} was there.")
        return " ".join(sentences)

    @staticmethod
    def __menu_talk(group: list[HeardLine], listener: HeardPerson) -> str:
        sentences: list[str] = []
        for line in group:
            npc, player = line.speaker.name, line.player
            overheard = not sentences and line.speaker.ref_id != listener.ref_id
            if line.asked:
                asked = f'{listener.name} overheard {player} ask {npc}' if overheard else f'{player} asked {npc}'
                sentences.append(f'{asked}: "{line.asked}" {npc} answered: "{line.words}"')
            else:
                said = f'{listener.name} overheard {npc} say to {player}' if overheard else f'{npc} said to {player}'
                sentences.append(f'{said}: "{line.words}"')
        return " ".join(sentences)

    @staticmethod
    def __to(line: HeardLine, hide_player: bool) -> HeardPerson | None:
        """Who the line was said to. Not the player while hide_player"""
        if line.to is None or (hide_player and line.to.is_player):
            return None
        return line.to

    @staticmethod
    def __when_and_where(line: HeardLine, now: GameTime | None) -> str:
        """'Earlier today in The Bannered Mare, '"""
        when = ""
        if now is not None:
            days_ago = now.calendar_day - line.game_time.calendar_day
            when = "Earlier today" if days_ago <= 0 else "Yesterday" if days_ago == 1 else f"{days_ago} days ago"
        if when and line.place:
            return f"{when} in {line.place}, "
        if when:
            return f"{when}, "
        if line.place:
            return f"In {line.place}, "
        return ""
