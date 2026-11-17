const BONUS_PATTERN = /(\bbonus\b|mega\s*ball|power\s*ball|cash\s*ball)/gi;
const BONUS_PATTERN_SINGLE = /(\bbonus\b|mega\s*ball|power\s*ball|cash\s*ball)/i;

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

export function hasBonusTerms(text = '') {
    return BONUS_PATTERN_SINGLE.test(String(text));
}

export function hasBonusSignal(message = {}) {
    const textHasBonus = hasBonusTerms(message.text || '');
    if (textHasBonus) {
        return true;
    }

    const toolResult = message.toolResult;
    if (toolResult && Array.isArray(toolResult.predicted_bonus_numbers) && toolResult.predicted_bonus_numbers.length > 0) {
        return true;
    }

    if (Array.isArray(message.sources) && message.sources.some((source) => hasBonusTerms(source?.content || ''))) {
        return true;
    }

    return false;
}

export function highlightBonusTermsInHtml(html = '') {
    return String(html).replace(BONUS_PATTERN, '<span class="bonus-token">$1</span>');
}

export function highlightBonusTermsAsSafeHtml(text = '') {
    return escapeHtml(text).replace(BONUS_PATTERN, '<span class="bonus-token">$1</span>');
}
