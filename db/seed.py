from __future__ import annotations

from sqlalchemy import select

from db.models import Language, Perk, PerkCategory, PerkTranslation
from db.session import SessionLocal

PERKS = [
    ("skin_noir_detective", PerkCategory.cosmetic,   500,  {"asset": "skins/noir_detective"}),
    ("name_color",          PerkCategory.cosmetic,   250,  {}),
    ("emote_pack_classic",  PerkCategory.cosmetic,   350,  {"count": 5}),
    ("weapon_golden",       PerkCategory.cosmetic,   800,  {"asset": "weapons/golden_revolver"}),
    ("night_vision",        PerkCategory.cosmetic,   450,  {}),
    ("xp_boost_x2",         PerkCategory.consumable, 300,  {"matches": 3, "multiplier": 2}),
    ("role_reveal_token",   PerkCategory.consumable, 600,  {"uses": 1}),
    ("revive_charm",        PerkCategory.consumable, 1000, {"uses": 1, "cooldown_days": 7}),
    ("event_pass",          PerkCategory.access,     750,  {"duration_days": 3}),
    ("vip_lobby_30d",       PerkCategory.subscription,1200,{"duration_days": 30}),
]

TRANSLATIONS = {
    "skin_noir_detective": {
        Language.en: ("Noir Detective Skin",          "A hard-boiled trench coat look for your character."),
        Language.es: ("Piel Detective Noir",          "Un estilo de gabardina noir para tu personaje."),
        Language.fr: ("Skin Détective Noir",          "Un look gabardine noir pour votre personnage."),
        Language.de: ("Noir-Detektiv-Skin",           "Ein Trenchcoat-Look im Noir-Stil für deinen Charakter."),
        Language.zh: ("黑色侦探皮肤",                   "为你的角色换上硬派风衣造型。"),
        Language.ru: ("Скин «Нуар-детектив»",           "Стильный плащ в духе нуара для вашего персонажа."),
    },
    "name_color": {
        Language.en: ("Custom Name Color",             "Colored nameplate in-game and on Telegram."),
        Language.es: ("Color de Nombre",               "Placa de nombre de color en el juego y en Telegram."),
        Language.fr: ("Couleur de Pseudo",             "Plaque de nom colorée en jeu et sur Telegram."),
        Language.de: ("Namensfarbe",                   "Farbiges Namensschild im Spiel und auf Telegram."),
        Language.zh: ("自定义名称颜色",                 "游戏与 Telegram 中的彩色名牌。"),
        Language.ru: ("Цвет ника",                     "Цветной ник в игре и в Telegram."),
    },
    "emote_pack_classic": {
        Language.en: ("Classic Emote Pack",            "5 animated emotes for the Day phase."),
        Language.es: ("Paquete de Emotes Clásico",     "5 emotes animados para la fase de Día."),
        Language.fr: ("Pack d'Emotes Classique",       "5 emotes animées pour la phase de Jour."),
        Language.de: ("Klassisches Emote-Paket",       "5 animierte Emotes für die Tag-Phase."),
        Language.zh: ("经典表情包",                     "5 个白天阶段可用的动画表情。"),
        Language.ru: ("Классический набор эмоций",     "5 анимированных эмоций для дневной фазы."),
    },
    "weapon_golden": {
        Language.en: ("Golden Revolver",               "Cosmetic weapon variant with unique animation."),
        Language.es: ("Revólver Dorado",               "Variante cosmética con animación única."),
        Language.fr: ("Revolver Doré",                 "Variante cosmétique avec animation unique."),
        Language.de: ("Goldener Revolver",             "Kosmetische Waffenvariante mit einzigartiger Animation."),
        Language.zh: ("黄金左轮",                       "独特动画的武器外观。"),
        Language.ru: ("Золотой револьвер",              "Косметический вариант оружия с уникальной анимацией."),
    },
    "night_vision": {
        Language.en: ("Night Vision Goggles",          "Special visual filter during the Night cycle."),
        Language.es: ("Gafas de Visión Nocturna",      "Filtro visual especial durante la Noche."),
        Language.fr: ("Lunettes de Vision Nocturne",   "Filtre visuel spécial pendant la Nuit."),
        Language.de: ("Nachtsichtgerät",               "Spezieller Visual-Filter in der Nacht-Phase."),
        Language.zh: ("夜视镜",                         "夜晚阶段的特殊画面滤镜。"),
        Language.ru: ("Прибор ночного видения",         "Особый визуальный фильтр во время ночной фазы."),
    },
    "xp_boost_x2": {
        Language.en: ("Double XP Boost",               "2x XP for the next 3 matches."),
        Language.es: ("Potenciador de XP x2",          "2x XP en las próximas 3 partidas."),
        Language.fr: ("Boost XP x2",                   "2x XP pour les 3 prochaines parties."),
        Language.de: ("Doppelter XP-Boost",            "2x XP für die nächsten 3 Spiele."),
        Language.zh: ("双倍经验",                       "接下来 3 局 2 倍经验。"),
        Language.ru: ("Двойной опыт",                   "x2 опыта на следующие 3 матча."),
    },
    "role_reveal_token": {
        Language.en: ("Role Reveal Token",             "Peek one random role at match start."),
        Language.es: ("Ficha de Revelación de Rol",    "Espía un rol aleatorio al inicio de la partida."),
        Language.fr: ("Jeton de Révélation",           "Révèle un rôle aléatoire au début de la partie."),
        Language.de: ("Rollen-Aufdeck-Token",          "Enthülle zu Spielbeginn eine zufällige Rolle."),
        Language.zh: ("身份揭示令牌",                   "开局时窥视一个随机身份。"),
        Language.ru: ("Жетон раскрытия роли",           "Подсмотреть одну случайную роль в начале матча."),
    },
    "revive_charm": {
        Language.en: ("Revive Charm",                  "Once per week: return as ghost-observer after elimination."),
        Language.es: ("Amuleto de Resurrección",       "Una vez por semana: vuelve como observador fantasma."),
        Language.fr: ("Charme de Résurrection",        "Une fois par semaine : reviens en observateur fantôme."),
        Language.de: ("Wiederbelebungs-Amulett",       "Einmal pro Woche: Rückkehr als Geist-Beobachter."),
        Language.zh: ("复活护符",                       "每周一次:淘汰后以幽灵观察者身份回归。"),
        Language.ru: ("Амулет возрождения",             "Раз в неделю: вернуться призраком-наблюдателем после гибели."),
    },
    "event_pass": {
        Language.en: ("Event Pass",                    "Entry to the weekend themed event."),
        Language.es: ("Pase de Evento",                "Entrada al evento temático de fin de semana."),
        Language.fr: ("Pass Événement",                "Accès à l'événement thématique du week-end."),
        Language.de: ("Event-Pass",                    "Zugang zum Wochenend-Themenevent."),
        Language.zh: ("活动通行证",                     "周末主题活动入场券。"),
        Language.ru: ("Пропуск на ивент",               "Доступ к тематическому ивенту выходного дня."),
    },
    "vip_lobby_30d": {
        Language.en: ("VIP Lobby (30 days)",           "Priority matchmaking and ranked-only rooms."),
        Language.es: ("Sala VIP (30 días)",            "Emparejamiento prioritario y salas clasificadas."),
        Language.fr: ("Salon VIP (30 jours)",          "Matchmaking prioritaire et salles classées."),
        Language.de: ("VIP-Lobby (30 Tage)",           "Priorisiertes Matchmaking und Ranked-Räume."),
        Language.zh: ("VIP 大厅(30 天)",              "优先匹配与排位专属房间。"),
        Language.ru: ("VIP-лобби (30 дней)",            "Приоритетный подбор и доступ к рейтинговым комнатам."),
    },
}


async def seed_catalog() -> None:
    async with SessionLocal() as s:
        existing = (await s.execute(select(Perk.code))).scalars().all()
        existing_set = set(existing)
        for code, category, cost, meta in PERKS:
            if code in existing_set:
                continue
            perk = Perk(code=code, category=category, cost_coins=cost, meta=meta)
            s.add(perk)
            await s.flush()
            for lang, (name, desc) in TRANSLATIONS[code].items():
                s.add(PerkTranslation(perk_id=perk.perk_id, language=lang, name=name, description=desc))
        await s.commit()
