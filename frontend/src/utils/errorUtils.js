export const ErrorCategory = {
    CONNECTION_ERROR: 'CONNECTION_ERROR',
    UNKNOWN_ERROR: 'UNKNOWN_ERROR',
};

const errorPatterns = {
    [ErrorCategory.CONNECTION_ERROR]: [
        /network error/i,
        /connection refused/i,
        /connection reset/i,
        /connection was closed/i,
        /err_empty_response/i,
        /err_connection_refused/i,
        /empty response/i,
        /502 bad gateway/i,
        /status code 502/i,
        /503 service unavailable/i,
        /status code 503/i,
        /504 gateway timeout/i,
        /status code 504/i,
        /timeout of \d+ms exceeded/i,
        /ECONNABORTED/i,
    ],
};

export function formatApiError(error, fallback = 'Request failed') {
    const data = error?.response?.data;
    if (!data) {
        return error?.message || fallback;
    }
    const detail = data.detail ?? data.message ?? data.error;
    if (Array.isArray(detail)) {
        return detail
            .map((item) => {
                if (item == null) return '';
                if (typeof item === 'string') return item;
                if (typeof item === 'object') {
                    const loc = Array.isArray(item.loc) ? item.loc.filter((p) => p !== 'body').join('.') : '';
                    const msg = item.msg || item.message || JSON.stringify(item);
                    return loc ? `${loc}: ${msg}` : msg;
                }
                return String(item);
            })
            .filter(Boolean)
            .join('; ');
    }
    if (detail && typeof detail === 'object') {
        return detail.message || detail.msg || JSON.stringify(detail);
    }
    return String(detail || error?.message || fallback);
}

export function analyzeError(error) {
    const errorMessage = formatApiError(error, error?.message || String(error));
    const statusCode = error?.response?.status;
    const isGatewayError = statusCode === 502 || statusCode === 503 || statusCode === 504;

    let category = isGatewayError ? ErrorCategory.CONNECTION_ERROR : ErrorCategory.UNKNOWN_ERROR;
    if (!isGatewayError) {
        for (const [cat, patterns] of Object.entries(errorPatterns)) {
            if (patterns.some((pattern) => pattern.test(errorMessage))) {
                category = cat;
                break;
            }
        }
    }

    const titles = {
        [ErrorCategory.CONNECTION_ERROR]: 'Connection Issue',
        [ErrorCategory.UNKNOWN_ERROR]: 'Unexpected Error',
    };

    const suggestions = {
        [ErrorCategory.CONNECTION_ERROR]: [
            'Wait 60-90 seconds after a container restart, then hard-refresh (Ctrl+Shift+R).',
            'Use http://127.0.0.1:3000 instead of http://localhost:3000.',
            'Ingestion can block the API for a minute; keep this tab open and wait.',
            'Restart Docker Desktop, then run: docker compose up -d --force-recreate',
            'Check containers: docker ps --filter name=pocketpro_nyl',
            'Review backend logs: docker logs pocketpro_nyl_backend --tail 50',
        ],
        [ErrorCategory.UNKNOWN_ERROR]: [
            'Check the browser console for more details.',
            'Try refreshing the page.',
            'Report this issue if it persists.',
        ],
    };

    return {
        category,
        title: titles[category],
        originalError: errorMessage,
        suggestions: suggestions[category],
    };
}
