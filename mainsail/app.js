'use strict'

const MATERIALS = ['PLA', 'PETG', 'ASA', 'ABS', 'TPU', 'PCTG', 'PA', 'PA-CF', 'PC', 'HIPS', 'PVA']
const EMPTY_MATERIALS = new Set(['', 'EMPTY', 'NONE', 'UNKNOWN', 'UNSET'])
const REFRESH_INTERVAL_MS = 5000

const toolsElement = document.querySelector('#tools')
const statusElement = document.querySelector('#status')
const template = document.querySelector('#tool-template')
const cards = new Map()

function normalizeColor(value) {
    const normalized = String(value ?? '').trim().replace(/^#/, '').slice(0, 6).toUpperCase()
    return /^[0-9A-F]{6}$/.test(normalized) ? `#${normalized}` : '#000000'
}

function normalizeMaterial(value) {
    const material = String(value ?? '').trim().toUpperCase()
    return EMPTY_MATERIALS.has(material) ? 'EMPTY' : material
}

function clampTemperature(value, maximum) {
    const number = Number.parseInt(value, 10)
    if (!Number.isFinite(number)) return 0
    return Math.max(0, Math.min(maximum, number))
}

function laneToolNumber(key) {
    const match = /^tool(\d+)$/i.exec(key)
    return match ? Number.parseInt(match[1], 10) : null
}

function setStatus(message, error = false) {
    statusElement.textContent = message
    statusElement.classList.toggle('error', error)
}

async function request(path, options = {}) {
    const response = await fetch(path, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json', ...(options.headers ?? {}) },
        ...options,
    })
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
    const payload = await response.json()
    if (payload.error) throw new Error(payload.error.message ?? 'Moonraker request failed')
    return payload
}

function markDirty(card) {
    card.dirty = true
    card.element.classList.add('is-dirty')
}

function setCardColor(card, color) {
    const normalized = normalizeColor(color)
    card.colorPicker.value = normalized
    card.colorHex.value = normalized.toUpperCase()
    card.colorDot.style.backgroundColor = normalized
}

function addMaterialOptions(select) {
    const emptyOption = document.createElement('option')
    emptyOption.value = 'EMPTY'
    emptyOption.textContent = 'Empty'
    select.append(emptyOption)
    for (const material of MATERIALS) {
        const option = document.createElement('option')
        option.value = material
        option.textContent = material
        select.append(option)
    }
}

function createCard(tool) {
    const element = template.content.firstElementChild.cloneNode(true)
    const card = {
        tool,
        element,
        heading: element.querySelector('h2'),
        colorDot: element.querySelector('.color-dot'),
        material: element.querySelector('.material'),
        colorPicker: element.querySelector('.color-picker'),
        colorHex: element.querySelector('.color-hex'),
        nozzleTemp: element.querySelector('.nozzle-temp'),
        bedTemp: element.querySelector('.bed-temp'),
        saveButton: element.querySelector('.save'),
        emptyButton: element.querySelector('.empty'),
        dirty: false,
        pending: false,
    }
    card.heading.textContent = tool === 0 && cards.size === 0 ? 'Extruder' : `Tool T${tool}`
    addMaterialOptions(card.material)

    card.material.addEventListener('change', () => markDirty(card))
    card.nozzleTemp.addEventListener('input', () => markDirty(card))
    card.bedTemp.addEventListener('input', () => markDirty(card))
    card.colorPicker.addEventListener('input', () => {
        setCardColor(card, card.colorPicker.value)
        markDirty(card)
    })
    card.colorHex.addEventListener('input', () => {
        if (!card.colorHex.value.startsWith('#')) card.colorHex.value = `#${card.colorHex.value.replace(/#/g, '')}`
        const color = normalizeColor(card.colorHex.value)
        if (/^#[0-9a-f]{6}$/i.test(card.colorHex.value)) {
            card.colorPicker.value = color
            card.colorDot.style.backgroundColor = color
        }
        markDirty(card)
    })
    card.colorHex.addEventListener('blur', () => setCardColor(card, card.colorHex.value))
    card.saveButton.addEventListener('click', () => saveCard(card, false))
    card.emptyButton.addEventListener('click', () => saveCard(card, true))

    cards.set(tool, card)
    toolsElement.append(element)
    return card
}

function updateHeadings() {
    const singleTool = cards.size === 1
    for (const card of cards.values()) card.heading.textContent = singleTool ? 'Extruder' : `Tool T${card.tool}`
}

function updateCard(card, lane) {
    if (card.dirty || card.pending) return
    const material = normalizeMaterial(lane.material)
    card.material.value = material === 'EMPTY' || MATERIALS.includes(material) ? material : 'PLA'
    setCardColor(card, lane.color)
    card.nozzleTemp.value = String(lane.nozzle_temp ?? 0)
    card.bedTemp.value = String(lane.bed_temp ?? 0)
    card.element.classList.remove('is-dirty')
}

async function saveCard(card, empty) {
    const material = card.material.value
    const color = normalizeColor(card.colorHex.value).slice(1)
    const nozzle = clampTemperature(card.nozzleTemp.value, 350)
    const bed = clampTemperature(card.bedTemp.value, 150)
    const isEmpty = empty || material === 'EMPTY'
    const script = isEmpty
        ? `SET_TOOL_FILAMENT TOOL=${card.tool} EMPTY=1 QUIET=1`
        : `SET_TOOL_FILAMENT TOOL=${card.tool} MATERIAL=${material} COLOR=${color} NOZZLE_TEMP=${nozzle} BED_TEMP=${bed} QUIET=1`

    card.pending = true
    card.saveButton.disabled = true
    card.emptyButton.disabled = true
    try {
        await request('/printer/gcode/script', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ script }),
        })
        card.dirty = false
        card.element.classList.remove('is-dirty')
        if (isEmpty) {
            card.material.value = 'EMPTY'
            setCardColor(card, '#000000')
            card.nozzleTemp.value = '0'
            card.bedTemp.value = '0'
        }
        setStatus('Saved')
        window.setTimeout(refresh, 1000)
    } catch (error) {
        setStatus(`Unable to save: ${error.message}`, true)
    } finally {
        card.pending = false
        card.saveButton.disabled = false
        card.emptyButton.disabled = false
    }
}

async function refresh() {
    try {
        const payload = await request('/server/database/item?namespace=lane_data')
        const lanes = payload.result?.value ?? {}
        const entries = Object.entries(lanes)
            .map(([key, lane]) => [laneToolNumber(key), lane])
            .filter(([tool, lane]) => tool !== null && lane && typeof lane === 'object')
            .sort(([left], [right]) => left - right)

        for (const [tool, lane] of entries) {
            const card = cards.get(tool) ?? createCard(tool)
            updateCard(card, lane)
        }
        for (const [tool, card] of cards) {
            if (!entries.some(([entryTool]) => entryTool === tool)) {
                card.element.remove()
                cards.delete(tool)
            }
        }
        updateHeadings()
        setStatus(entries.length ? 'Live sync active' : 'Waiting for filament-sync lane data')
    } catch (error) {
        setStatus(`Unable to read Moonraker: ${error.message}`, true)
    }
}

document.querySelector('#refresh').addEventListener('click', refresh)
refresh()
window.setInterval(refresh, REFRESH_INTERVAL_MS)
