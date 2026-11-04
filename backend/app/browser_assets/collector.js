var ShoppingAgentCollector = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	// src/extension/variant-controller.ts
	var MARKER = "data-shopping-agent-variant";
	var GROUPS = "[class*='skuItem--'], #J_isku dl, .tb-sku dl, #choose-attrs > .li, #choose [id^='choose-'], [data-sku-group]";
	var OPTIONS = "[class*='valueItem--'], dd li[data-value], .dd .item, [role='radio'], [data-sku-option]";
	var LABEL = "[class*='labelWrapTitle'], [class*='ItemLabel'], dt, .dt, [data-sku-label]";
	var text = (value, max = 512) => (value || "").replace(/\s+/g, " ").trim().slice(0, max);
	function visible(el) {
		const style = getComputedStyle(el);
		return style.display !== "none" && style.visibility !== "hidden" && el.getClientRects().length > 0;
	}
	function id(prefix, value) {
		let hash = 2166136261;
		for (const char of value) {
			hash ^= char.codePointAt(0);
			hash = Math.imul(hash, 16777619);
		}
		return `${prefix}-${(hash >>> 0).toString(16)}`;
	}
	function selected(el) {
		return [
			"aria-checked",
			"aria-selected",
			"aria-pressed",
			"data-selected"
		].some((a) => el.getAttribute(a) === "true") || /(?:^|\s)(?:(?:is|tb-)?selected|curr|checked)(?:--|\s|$)/i.test(el.className);
	}
	function disabled(el) {
		return el.hasAttribute("disabled") || ["aria-disabled", "data-disabled"].some((a) => /^(true|1)$/.test(el.getAttribute(a) || "")) || /(?:^|\s)(?:isDisabled|disabled|out-of-stock|no-stock)(?:--|\s|$)/i.test(el.className);
	}
	function safeOption(el, label) {
		if (!label || label.length > 512 || /购买|下单|购物车|支付|领取|客服|收藏|登录|注册|数量|件数/.test(label)) return false;
		if (el.matches("button") && el.closest("form") && el.getAttribute("type") !== "button") return false;
		if (!/^(DIV|LI|BUTTON|LABEL|SPAN|A)$/.test(el.tagName)) return false;
		const href = (el.matches("a") ? el : el.querySelector("a"))?.getAttribute("href")?.trim();
		return !href || /^(#|javascript:;?|javascript:void\(0\);?)$/i.test(href);
	}
	function inspectVariants() {
		document.querySelectorAll(`[${MARKER}]`).forEach((el) => el.removeAttribute(MARKER));
		const groups = [];
		let truncated = false, ambiguous = false, total = 0;
		const rows = Array.from(document.querySelectorAll(GROUPS)).filter(visible).filter((row) => !row.parentElement?.closest(GROUPS));
		for (const row of rows.slice(0, 12)) {
			const labelNode = row.querySelector(LABEL);
			const label = text(row.getAttribute("data-sku-group") || labelNode?.textContent, 128);
			if (!label) continue;
			const groupId = id("g", label);
			const options = [];
			const nodes = Array.from(row.querySelectorAll(OPTIONS)).filter(visible).filter((node) => !node.parentElement?.closest(OPTIONS));
			for (const el of nodes.slice(0, Math.min(40, 160 - total))) {
				const labelEl = el.querySelector("[class*='valueItemText'], [data-sku-text]");
				const value = text(labelEl?.getAttribute("title") || labelEl?.textContent || el.getAttribute("title") || el.getAttribute("aria-label") || el.textContent);
				if (!value) continue;
				const optionId = id("o", `${label}\n${value}\n${el.getAttribute("data-vid") || el.getAttribute("data-value") || ""}`);
				if (options.some((option) => option.id === optionId)) ambiguous = true;
				el.setAttribute(MARKER, `${groupId}:${optionId}`);
				options.push({
					id: optionId,
					label: value,
					selected: selected(el),
					disabled: disabled(el),
					selectable: safeOption(el, value),
					evidence_ids: []
				});
				total += 1;
			}
			if (nodes.length > options.length) truncated = true;
			if (!options.length) continue;
			if (groups.some((group) => group.id === groupId) || options.filter((option) => option.selected).length > 1) ambiguous = true;
			groups.push({
				id: groupId,
				label,
				options
			});
		}
		if (rows.length > 12 || total >= 160) truncated = true;
		return {
			status: ambiguous ? "ambiguous" : groups.length ? "ready" : "missing",
			groups,
			truncated
		};
	}
	function validateVariantTarget(groupId, optionId) {
		const catalog = inspectVariants();
		if (catalog.status !== "ready") return false;
		const option = catalog.groups.find((group) => group.id === groupId)?.options.find((option) => option.id === optionId);
		return Boolean(option && !option.disabled && option.selectable);
	}
	// src/extension/capture-adapters.ts
	var SELECTORS = {
		taobao: {
			title: [
				"#J_DetailMeta h1",
				".tb-detail-hd h1",
				"h1"
			],
			brand: [
				"#J_AttrUL",
				"#attributes",
				".J_AttrList"
			],
			seller: [
				".slogo-shopname",
				"[class*='shopName']",
				"[class*='shop-info']"
			],
			price: [
				"#J_PromoPrice",
				"#J_StrPrice",
				"[itemprop='price']",
				"[class*='price']"
			],
			sku: [
				"#J_isku",
				".tb-sku",
				"[class*='skuOptionsArea']"
			],
			specifications: [
				"#J_AttrUL",
				"#attributes",
				".J_AttrList",
				"[class*='attr-list']"
			],
			gallery: [
				"#J_ImgBooth",
				"#spec-list",
				".tb-gallery",
				"[class*='picGallery']"
			],
			details: [
				"#J_DivItemDesc",
				"#description",
				"#detailContent",
				"[class*='detail-content']"
			]
		},
		tmall: {
			title: [
				"[class*='SkuPanelItemhead'] h1",
				"[class*='SkuPanelItemhead'] [class*='title']",
				"h1"
			],
			brand: [
				"#J_AttrUL",
				"#attributes",
				".J_AttrList"
			],
			seller: [
				".slogo-shopname",
				"[class*='shopName']",
				"[class*='shop-info']"
			],
			price: [
				"[class*='SkuPanel'] [class*='price']",
				"#J_PromoPrice",
				".tm-price",
				"[itemprop='price']",
				"[class*='price']"
			],
			sku: [
				".skuOptionsArea",
				"[class*='skuOptionsArea']",
				"#J_isku",
				".tb-sku"
			],
			specifications: [
				".paramsInfoArea",
				"[class*='paramsWrap--']",
				"#J_AttrUL",
				"#attributes",
				".J_AttrList",
				"[class*='attr-list']",
				"[class*='attributes']",
				".page-content-area"
			],
			gallery: [
				".picGalleryEle",
				"#J_ImgBooth",
				"#spec-list",
				"[class*='picGallery']"
			],
			details: [
				"#J_DivItemDesc",
				"#description",
				"#detailContent",
				"[class*='descV8']",
				"[class*='detail-content']",
				"[class*='detailContent']",
				".page-content-area"
			]
		},
		jd: {
			title: [
				".sku-title-text",
				".sku-title-name",
				"#name h1",
				".sku-name",
				"[class*='skuName']",
				"h1"
			],
			brand: [
				"#parameter",
				".p-parameter",
				"[class*='parameter']",
				"[class*='brand']"
			],
			seller: [
				"#sellerName",
				".shop-name",
				"[class*='seller']",
				"[class*='shopName']"
			],
			price: [
				"#summary-price",
				".summary-price",
				"[itemprop='price']",
				".price_wrap",
				".price",
				"[class*='price']"
			],
			sku: [
				"#choose-attrs",
				"#choose",
				".itemInfo-wrap",
				".sku_choose_info",
				".sku_window",
				".sku_info",
				"[class*='sku']"
			],
			specifications: [
				"#parameter",
				".p-parameter",
				"[class*='parameter']",
				"[class*='attributes']",
				"[class*='spec']",
				"[class*='param']"
			],
			gallery: [
				"#spec-n1",
				"#spec-list",
				"#preview",
				"[class*='gallery']",
				"[class*='banner']"
			],
			details: [
				"#detail",
				"#detailContent",
				"[class*='detail-content']",
				"[class*='description']",
				"[class*='detail']"
			]
		}
	};
	var SENSITIVE_OR_IRRELEVANT_LABEL = /地址|收货|送至|配送|联系人|收件人|手机号|电话|运费|购买数量|数量|店铺活动|用户评价|问大家/i;
	var PRICE_PATTERN = /(?:￥|¥|CNY\s*)\s*([0-9][0-9,]*(?:\.\d{1,4})?)/i;
	var FONT_OBFUSCATED_PRICE = /[¥￥]\s*[\uE000-\uF8FF][\uE000-\uF8FF0-9.,]*/;
	var RECOMMENDATION_CONTEXTS = [
		"[class*='recommend' i]",
		"[class*='Recommend']",
		"[class*='guess' i]",
		"[class*='similar' i]",
		"[class*='hot_sale' i]",
		"[class*='product_floor']",
		"[class*='goods_floor']",
		"[class*='rank' i]",
		".mod_inspectyoupin",
		".mod_recommend"
	].join(",");
	var PRODUCT_SPEC_LABELS = /* @__PURE__ */ new Set([
		"品牌",
		"型号",
		"商品名称",
		"产品名称",
		"网线种类",
		"性能等级",
		"屏蔽结构",
		"使用环境",
		"绝缘材质",
		"护套材质",
		"连接设备",
		"线芯材质",
		"颜色分类",
		"长度",
		"接口类型",
		"适用设备",
		"传输速率",
		"包装",
		"版本类型",
		"材质",
		"规格型号",
		"产品类型",
		"插头类型",
		"线缆长度"
	]);
	var DETAIL_EXCLUDED_SELECTOR = [
		".picGalleryEle",
		"#J_ImgBooth",
		"#spec-list",
		".skuOptionsArea",
		"[class*='SkuPanel']",
		"[class*='recommend']",
		"[class*='Recommend']",
		"[class*='seller']",
		"[class*='shop']"
	].join(",");
	var SELECTED_STATE_SELECTOR = [
		"[aria-checked='true']",
		"[aria-selected='true']",
		"[aria-pressed='true']",
		"[data-selected='true']",
		"[class*='selected' i]",
		"[class*='active' i]",
		"[class*='checked' i]",
		".selected",
		".tb-selected",
		".sku-item-selected",
		".curr"
	].join(",");
	function cleanText(value, max = 512) {
		return (value ?? "").replace(/\s+/g, " ").trim().slice(0, max);
	}
	function normalizeMarketplaceTitle(value) {
		return cleanText(value).replace(/\s*[-|｜]\s*(?:(?:www\.)?(?:tmall|taobao|jd)\.com\s*)?(?:淘宝|天猫|京东)\s*$/i, "").trim();
	}
	function normalizeSellerName(value) {
		return cleanText(value, 512).replace(/\s*[|｜·•]?\s*(?:\d+(?:\.\d+)?(?:分)?|88VIP|好评率|次日达|同行客|客服满意度|描述相符|物流服务|发货速度).*$/i, "").replace(/[\s>›»]+$/, "").trim();
	}
	function selectedStateText(element) {
		const value = cleanText(element.getAttribute("aria-label") || element.getAttribute("title") || element.textContent, 161);
		return value.length <= 160 ? value : "";
	}
	function isVisible(element) {
		if (!(element instanceof HTMLElement)) return false;
		const style = getComputedStyle(element);
		return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0" && element.getClientRects().length > 0;
	}
	function firstVisibleText(document, selectors, max = 512) {
		for (const selector of selectors) for (const element of document.querySelectorAll(selector)) {
			if (!isVisible(element)) continue;
			const value = cleanText(element.textContent, max);
			if (value) return {
				value,
				locator: selector
			};
		}
		return null;
	}
	function productFromJsonLd(document) {
		const pending = [];
		for (const script of document.querySelectorAll("script[type='application/ld+json']")) {
			const text = script.textContent?.trim();
			if (!text || text.length > 131072) continue;
			try {
				pending.push(JSON.parse(text));
			} catch {}
		}
		let visited = 0;
		while (pending.length > 0 && visited < 300) {
			const value = pending.shift();
			visited += 1;
			if (Array.isArray(value)) {
				pending.push(...value.slice(0, 100));
				continue;
			}
			if (!value || typeof value !== "object") continue;
			const record = value;
			const type = record["@type"];
			if (typeof type === "string" && type.toLowerCase().split(/[\s,]+/).includes("product")) return record;
			if (Array.isArray(type) && type.some((entry) => typeof entry === "string" && entry.toLowerCase() === "product")) return record;
			for (const key of [
				"@graph",
				"mainEntity",
				"itemListElement",
				"item",
				"offers"
			]) {
				const child = record[key];
				if (child && typeof child === "object") pending.push(child);
			}
		}
		return null;
	}
	function jsonLdString(product, key) {
		const value = product?.[key];
		if (typeof value === "string") return cleanText(value);
		if (value && typeof value === "object" && "name" in value && typeof value.name === "string") return cleanText(value.name);
		return null;
	}
	function resolveProductTitle(document, pageTitle, product) {
		const visibleTitle = firstVisibleText(document, SELECTORS[capturePlatform(document)].title);
		const jsonLdTitle = jsonLdString(product, "name");
		const candidates = [
			{
				value: visibleTitle?.value ?? "",
				source: "visible_dom",
				locator: visibleTitle?.locator ?? ""
			},
			{
				value: jsonLdTitle ?? "",
				source: "json_ld",
				locator: "JSON-LD Product.name"
			},
			{
				value: pageTitle,
				source: "visible_dom",
				locator: "document.title"
			}
		];
		for (const candidate of candidates) {
			const value = normalizeMarketplaceTitle(candidate.value);
			if (value) return {
				...candidate,
				value
			};
		}
		return {
			value: "",
			source: "visible_dom",
			locator: ""
		};
	}
	function findAdjacentSpecificationValue(labelElement, container, label) {
		let current = labelElement;
		for (let depth = 0; current && current !== container && depth < 4; depth += 1) {
			const parentElement = current.parentElement;
			if (!parentElement) break;
			const siblings = Array.from(parentElement.children);
			const labelIndex = siblings.findIndex((sibling) => cleanText(sibling.textContent, 80).replace(/[：:]$/, "") === label);
			if (labelIndex >= 0) for (const index of [labelIndex + 1, labelIndex - 1]) {
				const sibling = siblings[index];
				if (!sibling || !isVisible(sibling)) continue;
				const rawValue = (sibling.textContent ?? "").replace(/\s+/g, " ").trim();
				if (!rawValue || rawValue.length > 256) continue;
				const value = cleanText(rawValue, 256);
				if (!value || PRODUCT_SPEC_LABELS.has(value.replace(/[：:]$/, ""))) continue;
				if (/^(?:复制|展开|收起|查看全部|请选择|请选择规格)$/i.test(value)) continue;
				return value;
			}
			current = parentElement;
		}
		return null;
	}
	function findSpecificationPairs(document, selectors) {
		const pairs = [];
		const seen = /* @__PURE__ */ new Set();
		for (const selector of selectors) for (const container of document.querySelectorAll(selector)) {
			if (!isVisible(container)) continue;
			const local = [];
			for (const [rowSelector, labelSelector, valueSelector] of [[
				"[class*='emphasisParamsInfoItem--']",
				"[class*='emphasisParamsInfoItemSubTitle--']",
				"[class*='emphasisParamsInfoItemTitle--']"
			], [
				"[class*='generalParamsInfoItem--']",
				"[class*='generalParamsInfoItemTitle--']",
				"[class*='generalParamsInfoItemSubTitle--']"
			]]) for (const row of container.querySelectorAll(rowSelector)) {
				const label = row.querySelector(labelSelector);
				const value = row.querySelector(valueSelector);
				if (!label || !value || !isVisible(label) || !isVisible(value)) continue;
				local.push({
					label: cleanText(label.textContent, 80),
					value: cleanText(value.textContent),
					locator: `${selector} ${rowSelector}`,
					ambiguous: Boolean(row.querySelector("[class*='aiAnnotatedText--']"))
				});
			}
			for (const label of container.querySelectorAll("dt")) {
				const value = label.nextElementSibling;
				if (value?.tagName.toLowerCase() !== "dd") continue;
				local.push({
					label: cleanText(label.textContent, 80),
					value: cleanText(value.textContent),
					locator: `${selector} dt + dd`
				});
			}
			for (const row of container.querySelectorAll("tr")) {
				const cells = Array.from(row.children).filter((cell) => ["th", "td"].includes(cell.tagName.toLowerCase()));
				if (cells.length < 2) continue;
				local.push({
					label: cleanText(cells[0].textContent, 80),
					value: cleanText(cells[1].textContent),
					locator: `${selector} tr`
				});
			}
			for (const item of container.querySelectorAll("li")) {
				const match = cleanText(item.textContent, 512).match(/^([^:：]{1,80})\s*[:：]\s*(.{1,400})$/);
				if (match) local.push({
					label: cleanText(match[1], 80),
					value: cleanText(match[2]),
					locator: `${selector} li`
				});
			}
			if (selector === ".page-content-area") for (const labelElement of Array.from(container.querySelectorAll("*")).slice(0, 3e3)) {
				const label = cleanText(labelElement.textContent, 80).replace(/[：:]$/, "");
				if (!PRODUCT_SPEC_LABELS.has(label) || !isVisible(labelElement)) continue;
				const value = findAdjacentSpecificationValue(labelElement, container, label);
				if (value) local.push({
					label,
					value,
					locator: `${selector} adjacent specification label`
				});
			}
			for (const pair of local) {
				const label = cleanText(pair.label, 80);
				const value = cleanText(pair.value, 512);
				const key = `${label}\u0000${value}`;
				if (!label || !value || SENSITIVE_OR_IRRELEVANT_LABEL.test(label) || seen.has(key)) continue;
				seen.add(key);
				pairs.push({
					...pair,
					label,
					value
				});
				if (pairs.length >= 100) return pairs;
			}
			if (pairs.length > 0) return pairs;
		}
		return pairs;
	}
	function capturePlatform(document) {
		const host = document.location.hostname.toLowerCase();
		if (host === "item.jd.com" || host === "item.m.jd.com" || host.endsWith(".jd.com")) return "jd";
		if (host === "item.taobao.com" || host.endsWith(".taobao.com")) return "taobao";
		return "tmall";
	}
	function fieldPathForSpec(index) {
		return `specs.attribute_${String(index + 1).padStart(2, "0")}`;
	}
	function imageUrlWithoutQuery(value, base) {
		try {
			const parsed = new URL(value, base);
			const host = parsed.hostname.toLowerCase();
			const isProductCdn = [
				"alicdn.com",
				"tbcdn.cn",
				"360buyimg.com"
			].some((suffix) => host === suffix || host.endsWith(`.${suffix}`));
			if (parsed.protocol !== "https:" || !isProductCdn || parsed.username || parsed.password) return null;
			parsed.search = "";
			parsed.hash = "";
			return parsed.toString().slice(0, 2048);
		} catch {
			return null;
		}
	}
	function collectCurrentProductSnapshot(document, pageUrl, pageTitle) {
		const platform = capturePlatform(document);
		const selectors = SELECTORS[platform];
		const product = productFromJsonLd(document);
		const evidence = [];
		const fields = [];
		const coverage = [];
		const images = [];
		const prices = [];
		function addEvidence(fieldPath, source, excerpt, locator) {
			const evidenceId = `e${String(evidence.length + 1).padStart(3, "0")}`;
			evidence.push({
				evidence_id: evidenceId,
				field_path: fieldPath,
				source,
				excerpt: cleanText(excerpt, 2048),
				locator: cleanText(locator, 256)
			});
			return evidenceId;
		}
		function addField(fieldPath, label, value, source = "visible_dom", locator = "", status = "observed") {
			if (!value) {
				fields.push({
					observation_id: `o${String(fields.length + 1).padStart(3, "0")}`,
					field_path: fieldPath,
					label,
					status: "missing",
					evidence_ids: []
				});
				return [];
			}
			const evidenceId = addEvidence(fieldPath, source, value, locator || source);
			fields.push({
				observation_id: `o${String(fields.length + 1).padStart(3, "0")}`,
				field_path: fieldPath,
				label,
				raw_value: value,
				normalized_value: value,
				status,
				evidence_ids: [evidenceId]
			});
			return [evidenceId];
		}
		const titleResult = resolveProductTitle(document, pageTitle, product);
		const title = titleResult.value;
		const titleEvidence = addField("product.title", "商品名称", title, titleResult.source, titleResult.locator || selectors.title.join(", "));
		coverage.push({
			field_path: "product.title",
			status: title ? "observed" : "missing",
			note: title ? void 0 : "未找到可核对的商品标题。",
			evidence_ids: titleEvidence
		});
		const pairs = findSpecificationPairs(document, selectors.specifications);
		const brandPair = pairs.find((pair) => /^(品牌|brand)$/i.test(pair.label));
		const jsonLdBrand = jsonLdString(product, "brand");
		const brandValue = brandPair?.value || jsonLdBrand;
		const brandEvidence = addField("product.brand", "品牌", brandValue, brandPair ? "visible_dom" : jsonLdBrand ? "json_ld" : "visible_dom", brandPair?.locator || "JSON-LD Product.brand", brandPair?.ambiguous ? "ambiguous" : "observed");
		coverage.push({
			field_path: "product.brand",
			status: brandValue ? brandPair?.ambiguous ? "ambiguous" : "observed" : "missing",
			note: brandPair?.ambiguous ? "页面将该项标注为平台 AI 提取，待核对。" : brandValue ? void 0 : "页面未提供可核对的品牌字段。",
			evidence_ids: brandEvidence
		});
		const seller = firstVisibleText(document, selectors.seller, 512);
		const sellerName = seller ? normalizeSellerName(seller.value) : "";
		const sellerEvidence = addField("seller.name", "店铺名称", sellerName || null, "visible_dom", seller?.locator || "");
		coverage.push({
			field_path: "seller.name",
			status: sellerName ? "observed" : "missing",
			note: sellerName ? void 0 : "未找到店铺名称。",
			evidence_ids: sellerEvidence
		});
		const specPairs = pairs.filter((pair) => pair !== brandPair);
		for (const [index, pair] of specPairs.entries()) {
			const fieldPath = fieldPathForSpec(index);
			const evidenceId = addEvidence(fieldPath, "visible_dom", `${pair.label}：${pair.value}`, pair.locator);
			fields.push({
				observation_id: `o${String(fields.length + 1).padStart(3, "0")}`,
				field_path: fieldPath,
				label: pair.label,
				raw_value: `${pair.label}：${pair.value}`,
				normalized_value: pair.value,
				status: pair.ambiguous ? "ambiguous" : "observed",
				evidence_ids: [evidenceId]
			});
			if (pair.ambiguous) coverage.push({
				field_path: fieldPath,
				status: "ambiguous",
				note: "页面将该参数标注为平台 AI 提取，待核对。",
				evidence_ids: [evidenceId]
			});
		}
		const specsEvidence = specPairs.map((_, index) => evidence.find((entry) => entry.field_path === fieldPathForSpec(index))?.evidence_id).filter((value) => Boolean(value)).slice(0, 20);
		coverage.push({
			field_path: "specifications",
			status: specPairs.some((pair) => pair.ambiguous) ? "ambiguous" : specPairs.length ? "observed" : "missing",
			note: specPairs.some((pair) => pair.ambiguous) ? "部分参数被页面标注为平台 AI 提取，待核对。" : specPairs.length ? void 0 : "页面规格区尚未识别；不会把详情宣传文案当作参数。",
			evidence_ids: specsEvidence
		});
		const catalog = inspectVariants();
		for (const group of catalog.groups) for (const option of group.options) option.evidence_ids = [addEvidence("variant_catalog", "visible_dom", `${group.label}：${option.label}；${option.selected ? "已选中" : "未选中"}；${option.disabled ? "页面禁用" : "页面未禁用"}`, `SKU group ${group.id}, option ${option.id}`)];
		coverage.push({
			field_path: "variant_catalog",
			status: catalog.truncated || catalog.status === "ambiguous" ? "ambiguous" : catalog.status === "ready" ? "observed" : "missing",
			note: catalog.truncated ? "只读取限定数量的选项，不能据此断言页面没有其他规格。" : catalog.status === "missing" ? "未找到可核对的规格选项区域。" : void 0,
			evidence_ids: []
		});
		if (catalog.groups.length) coverage.at(-1).evidence_ids = catalog.groups[0].options[0].evidence_ids;
		const skuContainer = selectors.sku.map((selector) => document.querySelector(selector)).find((candidate) => candidate && isVisible(candidate));
		const selectedOptionElements = skuContainer ? Array.from(skuContainer.querySelectorAll(SELECTED_STATE_SELECTOR)).filter(isVisible) : [];
		const selectedOptions = selectedOptionElements.map((element) => ({
			element,
			value: selectedStateText(element)
		})).filter(({ value }) => value && !/切换大图|关闭筛选|收藏|加入购物车|选择数量/.test(value)).filter(({ element, value }) => !selectedOptionElements.some((other) => {
			if (other === element || !element.contains(other)) return false;
			const childValue = selectedStateText(other);
			return Boolean(childValue && value.includes(childValue));
		})).map(({ value }) => value).filter((value, index, values) => values.indexOf(value) === index).slice(0, 12);
		const url = new URL(pageUrl);
		const skuId = url.searchParams.get("skuId") || url.searchParams.get("sku") || url.searchParams.get("wareId");
		const variantEvidenceIds = [];
		let variant;
		if (selectedOptions.length > 0) {
			const label = selectedOptions.join(" / ");
			variantEvidenceIds.push(addEvidence("selected_variant", "visible_dom", label, selectors.sku.join(", ")));
			variant = {
				status: "observed",
				label,
				evidence_ids: variantEvidenceIds
			};
		} else if (skuId) {
			const evidenceId = addEvidence("selected_variant.sku_id", "visible_dom", `页面 SKU 标识：${skuId}`, "URL search parameter");
			fields.push({
				observation_id: `o${String(fields.length + 1).padStart(3, "0")}`,
				field_path: "selected_variant.sku_id",
				label: "页面 SKU 标识",
				raw_value: skuId,
				normalized_value: skuId,
				status: "observed",
				evidence_ids: [evidenceId]
			});
			variantEvidenceIds.push(evidenceId);
			variant = {
				status: "ambiguous",
				evidence_ids: variantEvidenceIds
			};
		} else variant = {
			status: "missing",
			evidence_ids: []
		};
		if (catalog.status === "ready") {
			const attributes = catalog.groups.map((group) => ({
				group,
				options: group.options.filter((option) => option.selected)
			})).filter((row) => row.options.length === 1).map(({ group, options }, index) => ({
				observation_id: `v${String(index + 1).padStart(3, "0")}`,
				field_path: "selected_variant",
				label: group.label,
				raw_value: options[0].label,
				normalized_value: options[0].label,
				status: "observed",
				evidence_ids: options[0].evidence_ids
			}));
			if (attributes.length) variant = {
				status: attributes.length === catalog.groups.length ? "observed" : "ambiguous",
				label: attributes.map((attribute) => `${attribute.label}：${attribute.raw_value}`).join(" / ").slice(0, 512),
				attributes,
				evidence_ids: [...variantEvidenceIds, ...attributes.flatMap((attribute) => attribute.evidence_ids)].slice(0, 20)
			};
		}
		coverage.push({
			field_path: "selected_variant",
			status: variant.status,
			note: variant.status === "observed" ? void 0 : "未能从页面选中状态确认完整规格，SKU 标识不能代替规格文案。",
			evidence_ids: variant.evidence_ids ?? []
		});
		const priceTexts = /* @__PURE__ */ new Set();
		for (const selector of selectors.price) {
			for (const element of document.querySelectorAll(selector)) {
				if (!isVisible(element)) continue;
				if (element.closest(RECOMMENDATION_CONTEXTS)) continue;
				const raw = cleanText(element.textContent, 160);
				if (!(PRICE_PATTERN.test(raw) || FONT_OBFUSCATED_PRICE.test(raw)) || raw.length > 160 || priceTexts.has(raw)) continue;
				priceTexts.add(raw);
				if (priceTexts.size >= 3) break;
			}
			if (priceTexts.size > 0) break;
		}
		for (const [index, rawText] of [...priceTexts].slice(0, 8).entries()) {
			const evidenceId = addEvidence(`prices[${index}]`, "visible_dom", rawText, selectors.price.join(", "));
			const literal = PRICE_PATTERN.exec(rawText)?.[1]?.replace(/,/g, "") ?? null;
			const masked = /[¥￥]\s*[0-9][0-9,]*(?:\.\d{1,4})?\s*[?？*]/.test(rawText) || /[¥￥]\s*[?？*]/.test(rawText);
			const fontObfuscated = FONT_OBFUSCATED_PRICE.test(rawText);
			const amount = masked || fontObfuscated ? null : literal;
			const kind = /会员|88VIP/i.test(rawText) ? "member" : /券后|优惠券/i.test(rawText) ? "coupon" : /优惠前|划线价|原价/i.test(rawText) ? "list" : "unknown";
			prices.push({
				observation_id: `o${String(fields.length + index + 1).padStart(3, "0")}`,
				kind,
				raw_text: rawText,
				amount,
				currency: /￥|¥|CNY/i.test(rawText) ? "CNY" : void 0,
				status: masked || fontObfuscated ? "ambiguous" : "observed",
				conditions: [],
				evidence_ids: [evidenceId]
			});
		}
		const priceEvidence = evidence.filter((entry) => entry.field_path.startsWith("prices[")).map((entry) => entry.evidence_id);
		const maskedPrice = prices.some((price) => price.status === "ambiguous");
		const obfuscatedPrice = prices.some((price) => FONT_OBFUSCATED_PRICE.test(price.raw_text ?? ""));
		coverage.push({
			field_path: "price",
			status: prices.length ? maskedPrice ? "ambiguous" : "observed" : "missing",
			note: prices.length ? obfuscatedPrice ? "价格数字以自定义字体渲染，页面文字不含可读数字；金额需从实际显示读取。" : maskedPrice ? "页面价格包含被平台遮挡的数字，无法确认真实金额；不以部分数字推算。通常需要登录后才有完整价格。" : "仅记录页面可见价格文字；不推断券后或最终支付价。" : "未找到可核对的价格文字。",
			evidence_ids: priceEvidence
		});
		function collectImages(kind, candidates) {
			const container = candidates.map((selector) => document.querySelector(selector)).find((candidate) => candidate && isVisible(candidate));
			if (!container) {
				coverage.push({
					field_path: `images.${kind}`,
					status: "missing",
					note: kind === "detail" ? "未找到商品详情图片区域。" : "未找到商品主图区域。",
					evidence_ids: []
				});
				return;
			}
			const imageEvidence = [];
			const imageCandidates = Array.from(container.querySelectorAll("img"));
			const limit = Math.max(0, 40 - images.length);
			let eligible = 0;
			for (const image of imageCandidates) {
				if (!isVisible(image)) continue;
				if (kind === "detail") {
					if (image.closest(DETAIL_EXCLUDED_SELECTOR)) continue;
					if (image.complete && (image.naturalWidth < 240 || image.naturalHeight < 240)) continue;
				}
				const sourceUrl = imageUrlWithoutQuery(image.currentSrc || image.src, pageUrl);
				if (!sourceUrl) continue;
				eligible += 1;
				if (imageEvidence.length >= limit) continue;
				const loaded = image.complete && image.naturalWidth > 0;
				const index = images.length;
				const evidenceId = addEvidence(`images.${kind}[${index}]`, "image_metadata", `${cleanText(image.alt, 512) || "无替代文本"} · ${loaded ? `${image.naturalWidth}×${image.naturalHeight}` : "尚未加载"}`, `${candidates.join(", ")} img`);
				imageEvidence.push(evidenceId);
				images.push({
					index,
					source_url: sourceUrl,
					alt_text: cleanText(image.alt, 512) || null,
					loaded,
					width: loaded ? image.naturalWidth : null,
					height: loaded ? image.naturalHeight : null,
					status: loaded ? "observed" : "missing",
					evidence_ids: [evidenceId]
				});
			}
			const loadedCount = images.filter((image) => image.status === "observed" && (image.evidence_ids ?? []).some((id) => imageEvidence.includes(id))).length;
			const truncated = eligible > imageEvidence.length;
			coverage.push({
				field_path: `images.${kind}`,
				status: truncated ? "ambiguous" : loadedCount ? "observed" : "missing",
				note: `${loadedCount ? `记录 ${loadedCount} 张已加载图片；当前只保存来源 URL 和尺寸，不保存图片字节。` : "图片区域尚无已记录的可用图片。"}${truncated ? `发现 ${eligible} 张符合规则的图片，受本次总量上限限制，仅记录 ${imageEvidence.length} 张；不代表完整读取。` : ""}`,
				evidence_ids: imageEvidence.slice(0, 20)
			});
		}
		collectImages("gallery", selectors.gallery);
		collectImages("detail", selectors.details);
		const canonicalUrl = document.querySelector("link[rel='canonical']")?.href || null;
		const statuses = [
			...fields.map((field) => field.status),
			variant.status,
			...prices.map((price) => price.status),
			...images.map((image) => image.status),
			...coverage.map((entry) => entry.status)
		];
		const status = statuses.some((value) => value === "blocked") ? "blocked" : statuses.every((value) => value === "observed" || value === "user_confirmed" || value === "not_applicable") ? "complete" : "partial";
		return {
			schema_version: 1,
			adapter_id: `${platform}-dom`,
			adapter_version: "0.3.3",
			page_url: pageUrl,
			canonical_url: canonicalUrl,
			captured_at: (/* @__PURE__ */ new Date()).toISOString(),
			status,
			fields,
			selected_variant: variant,
			variant_catalog: catalog,
			prices,
			images,
			evidence,
			coverage
		};
	}
	// src/extension/capture-preparation.ts
	/** A bounded observation, never a promise that the whole product page was read. */
	async function prepareDetails(host, options = { scroll: true }) {
		const deadline = host.now() + Math.min(12e3, Math.max(0, options.budgetMs ?? 12e3));
		const maxSteps = Math.min(24, Math.max(0, options.maxSteps ?? 24));
		let steps = 0;
		let previous = "";
		let stableSince = host.now();
		try {
			host.check();
			if (!options.scroll) return {
				reason: "read_only",
				steps,
				detail: host.inspect()
			};
			while (true) {
				host.check();
				const detail = host.inspect();
				if (detail.signature !== previous || !detail.atEnd || detail.pending > 0) {
					previous = detail.signature;
					stableSince = host.now();
				}
				if (detail.found && detail.atEnd && !detail.pending && host.now() - stableSince >= 1e3) return {
					reason: "settled",
					steps,
					detail
				};
				if (host.now() >= deadline) return {
					reason: "time_limit",
					steps,
					detail
				};
				host.status(`正在读取详情区：${detail.loaded} 张图片已加载，${detail.pending} 张待加载…`);
				if (!detail.atEnd) {
					if (steps >= maxSteps) return {
						reason: "step_limit",
						steps,
						detail
					};
					host.advance();
					steps += 1;
				}
				await host.wait(Math.min(300, deadline - host.now()));
			}
		} finally {
			host.restore();
		}
	}
	function addPreparationCoverage(snapshot, result) {
		const { detail, reason, steps } = result;
		const conclusion = reason === "settled" ? "已滚动到已识别详情区末端，已发现图片在短暂观察期内稳定；不保证页面完整。" : reason === "read_only" ? "本次仅读取当前已加载内容，没有滚动加载详情。" : reason === "step_limit" ? "已达到滚动次数上限，后续详情可能尚未加载。" : "已达到等待期限，后续详情可能尚未加载。";
		snapshot.coverage = (snapshot.coverage ?? []).filter((entry) => entry.field_path !== "page.detail_preparation");
		snapshot.coverage.push({
			field_path: "page.detail_preparation",
			status: "ambiguous",
			evidence_ids: [],
			note: `${conclusion} ${detail.found ? "已识别详情区域" : "未识别详情区域"}；滚动 ${steps} 次；发现 ${detail.loaded} 张已加载、${detail.pending} 张待加载图片。图片文字识别单独处理。`
		});
		if (snapshot.status === "complete") snapshot.status = "partial";
		return snapshot;
	}
	// src/extension/spec-panel.ts
	/** Reveal a collapsed parameter table before the page snapshot is taken.
	*
	* Marketplace item pages can keep the specification table behind a tab that only renders
	* on click. Reading the DOM without that click records "specifications missing" even
	* though the data is one interaction away, so the table is expanded first.
	*
	* Only tab-shaped elements are clicked. A control that navigates elsewhere is skipped,
	* because the snapshot must keep describing the same product page.
	*/
	var SPEC_TAB_LABELS = [
		"规格参数",
		"商品参数",
		"产品参数",
		"参数规格",
		"规格与包装"
	];
	var DEFAULT_WAIT_MS = 1200;
	function isTabLike(element) {
		if (![
			"BUTTON",
			"DIV",
			"SPAN",
			"LI"
		].includes(element.tagName)) return false;
		if (element.closest("a, [href], form")) return false;
		if (element.hasAttribute("disabled") || element.getAttribute("aria-disabled") === "true") return false;
		const style = getComputedStyle(element);
		if (style.display === "none" || style.visibility === "hidden") return false;
		return element.getBoundingClientRect().width > 0;
	}
	function findSpecTab(document) {
		const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
		const candidates = [];
		let node = walker.nextNode();
		while (node) {
			if (node instanceof HTMLElement && SPEC_TAB_LABELS.includes((node.textContent || "").trim())) {
				if (isTabLike(node)) candidates.push(node);
			}
			node = walker.nextNode();
		}
		return candidates[0] ?? null;
	}
	/** Click the specification tab when present; return its label, or null when none applies. */
	async function expandSpecPanel(document, waitMs = DEFAULT_WAIT_MS) {
		const tab = findSpecTab(document);
		if (!tab) return null;
		const label = (tab.textContent || "").trim();
		const before = document.body.innerText.length;
		try {
			tab.click();
		} catch {
			return null;
		}
		await new Promise((resolve) => setTimeout(resolve, waitMs));
		return document.body.innerText.length > before ? label : null;
	}
	// src/extension/page-capture.ts
	var DETAIL_SELECTORS = [
		"#J_DivItemDesc",
		"#description",
		"#detailContent",
		"[class*='descV8']",
		"[class*='detail-content']",
		"#detail"
	];
	/** Read a user-opened page; no marketplace fetches, navigation, or SKU changes. */
	async function captureOpenedProduct(pageUrl, options) {
		const view = document.defaultView;
		const origin = {
			x: view.scrollX,
			y: view.scrollY
		};
		const status = options.status ?? (() => {});
		const selection = () => JSON.stringify(inspectVariants().groups.map((group) => [group.label, group.options.filter((option) => option.selected).map((option) => option.label)]));
		const initialSelection = selection();
		let interrupted = false;
		const interrupt = (event) => {
			if (event.target instanceof Element && event.target.closest("shopping-agent-product-assistant")) return;
			interrupted = true;
		};
		const interruptKey = (event) => {
			if ([
				"ArrowUp",
				"ArrowDown",
				"PageUp",
				"PageDown",
				"Home",
				"End",
				" "
			].includes(event.key)) interrupt(event);
		};
		const check = () => {
			if (options.signal?.aborted) throw new Error("采集已取消，未提交商品快照。");
			if (location.href !== pageUrl) throw new Error("商品页已变化，本次快照未提交，请在当前商品页重试。");
			if (interrupted) throw new Error("检测到页面操作，采集已停止，请选好规格后重新读取。");
			if (options.scroll && document.visibilityState !== "visible") throw new Error("商品页已切到后台，采集已停止，请回到此页重新读取。");
			if (selection() !== initialSelection) throw new Error("选中规格在采集期间发生变化，请等待价格更新后重新读取。");
		};
		const wait = async (ms) => {
			const end = performance.now() + ms;
			do {
				check();
				await new Promise((resolve) => setTimeout(resolve, Math.min(100, Math.max(0, end - performance.now()))));
			} while (performance.now() < end);
			check();
		};
		const container = () => DETAIL_SELECTORS.map((selector) => document.querySelector(selector)).find((node) => node && node.getClientRects().length && getComputedStyle(node).visibility !== "hidden");
		const images = () => Array.from(container()?.querySelectorAll("img") ?? []);
		const inspect = () => {
			const root = container();
			const candidates = images();
			const loaded = candidates.filter((image) => image.complete && image.naturalWidth > 0).length;
			const bottom = root ? root.getBoundingClientRect().bottom : document.documentElement.scrollHeight - view.scrollY;
			return {
				found: Boolean(root),
				atEnd: bottom <= view.innerHeight + 8,
				loaded,
				pending: candidates.length - loaded,
				signature: JSON.stringify([
					Boolean(root),
					Math.round(bottom + view.scrollY),
					candidates.map((image) => [
						image.currentSrc || image.src,
						image.complete,
						image.naturalWidth
					])
				])
			};
		};
		const eventOptions = {
			capture: true,
			passive: true
		};
		if (options.scroll) {
			view.addEventListener("wheel", interrupt, eventOptions);
			view.addEventListener("touchstart", interrupt, eventOptions);
			view.addEventListener("pointerdown", interrupt, eventOptions);
			view.addEventListener("keydown", interruptKey, true);
		}
		try {
			check();
			status("正在展开规格参数…");
			const expanded = options.scroll ? await expandSpecPanel(document) : null;
			check();
			status(expanded ? `已展开“${expanded}”，正在加载详情…` : "正在检查详情区…");
			const preparation = await prepareDetails({
				now: () => performance.now(),
				check,
				wait,
				inspect,
				status,
				advance: () => view.scrollTo({
					top: view.scrollY + Math.max(200, view.innerHeight * .75),
					behavior: "instant"
				}),
				restore: () => {
					if (options.scroll && !interrupted && location.href === pageUrl && document.visibilityState === "visible") view.scrollTo({
						left: origin.x,
						top: origin.y,
						behavior: "instant"
					});
				}
			}, options);
			check();
			status("正在核对选中规格和价格是否稳定…");
			const first = collectCurrentProductSnapshot(document, pageUrl, document.title);
			await wait(500);
			const snapshot = collectCurrentProductSnapshot(document, pageUrl, document.title);
			check();
			const stamp = (value) => JSON.stringify([
				value.selected_variant.status,
				value.selected_variant.label,
				value.prices?.map((price) => [
					price.raw_text,
					price.amount,
					price.kind
				])
			]);
			if (stamp(first) !== stamp(snapshot)) throw new Error("规格或价格仍在更新，本次快照未提交，请稍后重新读取。");
			return addPreparationCoverage(snapshot, preparation);
		} finally {
			view.removeEventListener("wheel", interrupt, true);
			view.removeEventListener("touchstart", interrupt, true);
			view.removeEventListener("pointerdown", interrupt, true);
			view.removeEventListener("keydown", interruptKey, true);
		}
	}
	// src/extension/backend-collector.ts
	function collect() {
		return collectCurrentProductSnapshot(document, location.href, document.title);
	}
	exports.captureOpenedProduct = captureOpenedProduct;
	exports.collect = collect;
	exports.inspectVariants = inspectVariants;
	exports.validateVariantTarget = validateVariantTarget;
	return exports;
})({});
