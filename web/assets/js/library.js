/**
 * @file library.js
 * Shared client-side utilities: HTTP abstraction and multilingual dictionary.
 *
 * Request    – static façade over the Fetch API for JSON and multipart requests.
 * Dictionary – singleton i18n manager that lazily loads locale JSON files and
 *              translates DOM elements decorated with data-* attributes.
 */

/**
 * Static HTTP client wrapping the Fetch API.
 *
 * All methods target `lib/api.php` by default but accept an optional `url`
 * override.  Responses are always parsed as JSON and returned directly.
 */
class Request{
    static _url = "lib/api.php";
    /**
     * Send a GET request with `data` serialised as JSON in the request body.
     * @param {Object} data - Payload to send.
     * @param {string} [url=""] - Target URL; defaults to `Request._url`.
     * @returns {Promise<*>} Parsed JSON response.
     */
    static async GET(data, url=""){
        return await this.#genericRequest(data, url, 'GET');
    }
    /**
     * Send a POST request with `data` serialised as JSON.
     * @param {Object} data - Payload to send.
     * @param {string} [url=""] - Target URL; defaults to `Request._url`.
     * @returns {Promise<*>} Parsed JSON response.
     */
    static async POST(data, url=""){
        return await this.#genericRequest(data, url, 'POST');
    }
    /**
     * Send a POST request with `data` encoded as multipart/form-data (for file uploads).
     * @param {Object} data - Key/value pairs appended to a FormData object.
     * @param {string} [url=""] - Target URL; defaults to `Request._url`.
     * @returns {Promise<*>} Parsed JSON response.
     */
    static async FILE(data, url=""){
        const target = (url == "") ? this._url : url;
        const formData = new FormData();
        for(let key in data){
            formData.append(key, data[key]);
        }
        const rawResponse = await fetch(target, {
            method: "POST",
            body: formData
          });
        const content = await rawResponse.json();
        return content;
    }
    /**
     * Internal helper that performs the actual fetch with JSON headers.
     * @param {Object} data   - Payload serialised as JSON.
     * @param {string} url    - Resolved target URL.
     * @param {string} method - HTTP method string (e.g. `"GET"` or `"POST"`).
     * @returns {Promise<*>} Parsed JSON response.
     */
    static async #genericRequest(data, url, method){
        const target = (url == "") ? this._url : url;
        const rawResponse = await fetch(target, {
            method: method,
            headers: {
              'Accept': 'application/json',
              'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
          });
        const content = await rawResponse.json();
        return content;
    }
}

/**
 * Singleton i18n manager with lazy-loaded locale files and DOM translation.
 *
 * Locale JSON files are fetched from `assets/locales/<lang>.json` on first use
 * and cached for the lifetime of the page.  DOM elements are translated by
 * reading `data-text`, `data-content`, `data-content-template`, `data-placeholder`,
 * `data-title`, and `data-title-template` attributes.
 *
 * The active language is persisted in `localStorage` under `"preferred-lang"` and
 * initialised from `navigator.languages` on first page load.
 */
class Dictionary{
    static #language = 'en';
    static #cache = {
        bg : null,
        cs : null,
        da : null,
        de : null,
        el : null,
        en : null,
        es : null,
        et : null,
        fi : null,
        fr : null,
        ga : null,
        hr : null,
        hu : null,
        it : null,
        lt : null,
        lv : null,
        mt : null,
        nl : null,
        pl : null,
        pt : null,
        ro : null,
        sk : null,
        sl : null,
        sv : null
    }

    /**
     * Detect the best-supported browser language, apply a stored preference if
     * present, and trigger an initial translation of the current document.
     * Called automatically when the script is loaded.
     */
    static setup(){
        const languages = navigator.languages;
        const supportedLanguages = Object.keys(Dictionary.#cache);
        let selectedLanguage = 'en';
        for(const language of languages){
            if(supportedLanguages.includes(language)){
                selectedLanguage = language;
                break;
            }
        }
        if(localStorage.getItem("preferred-lang") !== null){
            selectedLanguage = localStorage.getItem("preferred-lang");
        }
        Dictionary.#language = selectedLanguage;
        Dictionary.translate(null);
    }

    /**
     * Switch the active language and re-translate all data-* elements in the DOM.
     *
     * Passing `null` re-applies the current language (useful on initial load).
     * Dispatches a `languageChanged` CustomEvent on `window` after translation.
     *
     * @param {string|null} language - BCP-47 language tag (e.g. `"cs"`), or `null`
     *                                 to reuse the already-selected language.
     */
    static async translate(language){
        if(language == null){
            language = Dictionary.#language;
        }
        else{
            localStorage.setItem("preferred-lang", language);
        }
        if(!Object.keys(Dictionary.#cache).includes(language)){
            console.error("Not supported language");
            return;
        }
        await Dictionary.#ensureCache(language);
        Dictionary.#language = language;
        const terms = document.querySelectorAll("[data-text]");
        terms.forEach(term => {
            term.innerHTML = `${term.dataset.prefix ?? ""}${Dictionary.#term(term.dataset.text, language)}${term.dataset.postfix ?? ""}`;
        });
        const textTerms = document.querySelectorAll("[data-content]");
        textTerms.forEach(term => {
            term.textContent = `${term.dataset.prefix ?? ""}${Dictionary.#term(term.dataset.content, language)}${term.dataset.postfix ?? ""}`;
        });
        const textTemplateTerms = document.querySelectorAll("[data-content-template]");
        textTemplateTerms.forEach(term => {
            const translation = term.dataset.contentTemplate.replace(/\{\{([^\{]+)\}\}/g, (fullMatch, key) => {
                return Dictionary.#term(key.trim(), language); 
            });
            term.textContent = `${term.dataset.prefix ?? ""}${translation}${term.dataset.postfix ?? ""}`;
        });
        const placeholderTerms = document.querySelectorAll("[data-placeholder]");
        placeholderTerms.forEach(term => {
            term.placeholder = `${term.dataset.prefix ?? ""}${Dictionary.#term(term.dataset.placeholder, language)}${term.dataset.postfix ?? ""}`;
        });
        const titleTerms = document.querySelectorAll("[data-title]");
        titleTerms.forEach(term => {
            term.title = `${term.dataset.prefix ?? ""}${Dictionary.#term(term.dataset.title, language)}${term.dataset.postfix ?? ""}`;
        });
        const titleTemplateTerms = document.querySelectorAll("[data-title-template]");
        titleTemplateTerms.forEach(term => {
            const translation = term.dataset.titleTemplate.replace(/\{\{([^\{]+)\}\}/g, (fullMatch, key) => {
                return Dictionary.#term(key.trim(), language); 
            });
            term.title = `${term.dataset.prefix ?? ""}${translation}${term.dataset.postfix ?? ""}`;
        });

        window.dispatchEvent(new CustomEvent('languageChanged', { detail: language }));
    }

    /**
     * Look up a translation key in the currently active locale (synchronous).
     * @param {string} term - Dot-separated key path (e.g. `"nav.cohesion"`).
     * @returns {string} Translated string, or `""` if the key is not found.
     */
    static term(term){
        return Dictionary.#term(term, Dictionary.#language);
    }

    /**
     * Fetch and cache the locale file for `language` if it has not been loaded yet.
     * @param {string} language - BCP-47 language tag.
     */
    static async #ensureCache(language){
        if(Dictionary.#cache[language] === null){
            try{
                const response = await fetch(`assets/locales/${language}.json`);
                if (!response.ok) throw new Error(`HTTP error! status: ${response.ok}`);
                Dictionary.#cache[language] = await response.json();
            }
            catch{
                console.error("Error loading dictionary");
            }
        }
    }

    /**
     * Resolve a dot-separated key path against the cached locale object.
     * @param {string} term     - Dot-separated translation key.
     * @param {string} language - Locale to look up in the cache.
     * @returns {string} Translated value or `""`.
     */
    static #term(term, language){
        const path = term.split(".");
        let termObj = Dictionary.#cache[language];
        for(let i = 0; i < path.length; i++){
            termObj = termObj[path[i]] ?? "";
        }
        return termObj;
    }

    /**
     * Return the full locale object for the active language, loading it if needed.
     * @returns {Promise<Object>} The parsed locale JSON.
     */
    static async get(){
        await Dictionary.#ensureCache(Dictionary.#language);
        return Dictionary.#cache[Dictionary.#language];
    }

    /**
     * The BCP-47 tag of the currently active language.
     * @type {string}
     */
    static get language(){
        return Dictionary.#language;
    }
}
Dictionary.setup();