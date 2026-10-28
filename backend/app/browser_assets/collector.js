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
				"[class*='detail-content']",
				"[class*='detailContent']",
				".page-content-area"
			]
		},
		jd: {
			title: [
				"#name h1",
				".sku-name",
				"h1"
			],
			brand: [
				"#parameter",
				".p-parameter",
				"[class*='parameter']"
			],
			seller: [
				"#sellerName",
				".shop-name",
				"[class*='seller']"
			],
			price: [
				"#summary-price",
				".summary-price",
				"[itemprop='price']",
				"[class*='price']"
			],
			sku: [
				"#choose-attrs",
				"#choose",
				".itemInfo-wrap",
				"[class*='sku']"
			],
			specifications: [
				"#parameter",
				".p-parameter",
				"[class*='parameter']",
				"[class*='attributes']"
			],
			gallery: [
				"#spec-list",
				"#preview",
				"[class*='gallery']"
			],
			details: [
				"#detail",
				"#detailContent",
				"[class*='detail-content']",
				"[class*='description']"
			]
		}
	};
	var SENSITIVE_OR_IRRELEVANT_LABEL = /地址|收货|送至|配送|联系人|收件人|手机号|电话|运费|购买数量|数量|店铺活动|用户评价|问大家/i;
	var PRICE_PATTERN = /(?:￥|¥|CNY\s*)\s*([0-9][0-9,]*(?:\.\d{1,4})?)/i;
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
		function addField(fieldPath, label, value, source = "visible_dom", locator = "") {
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
				status: "observed",
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
		const brandEvidence = addField("product.brand", "品牌", brandValue, brandPair ? "visible_dom" : jsonLdBrand ? "json_ld" : "visible_dom", brandPair?.locator || "JSON-LD Product.brand");
		coverage.push({
			field_path: "product.brand",
			status: brandValue ? "observed" : "missing",
			note: brandValue ? void 0 : "页面未提供可核对的品牌字段。",
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
				status: "observed",
				evidence_ids: [evidenceId]
			});
		}
		const specsEvidence = specPairs.map((_, index) => evidence.find((entry) => entry.field_path === fieldPathForSpec(index))?.evidence_id).filter((value) => Boolean(value));
		coverage.push({
			field_path: "specifications",
			status: specPairs.length ? "observed" : "missing",
			note: specPairs.length ? void 0 : "页面规格区尚未识别；不会把详情宣传文案当作参数。",
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
				const raw = cleanText(element.textContent, 160);
				if (!PRICE_PATTERN.test(raw) || raw.length > 160 || priceTexts.has(raw)) continue;
				priceTexts.add(raw);
			}
			if (priceTexts.size > 0) break;
		}
		for (const [index, rawText] of [...priceTexts].slice(0, 8).entries()) {
			const evidenceId = addEvidence(`prices[${index}]`, "visible_dom", rawText, selectors.price.join(", "));
			const amount = PRICE_PATTERN.exec(rawText)?.[1]?.replace(/,/g, "") ?? null;
			const kind = /会员|88VIP/i.test(rawText) ? "member" : /券后|优惠券/i.test(rawText) ? "coupon" : /优惠前|划线价|原价/i.test(rawText) ? "list" : "unknown";
			prices.push({
				observation_id: `o${String(fields.length + index + 1).padStart(3, "0")}`,
				kind,
				raw_text: rawText,
				amount,
				currency: /￥|¥|CNY/i.test(rawText) ? "CNY" : void 0,
				status: "observed",
				conditions: [],
				evidence_ids: [evidenceId]
			});
		}
		const priceEvidence = evidence.filter((entry) => entry.field_path.startsWith("prices[")).map((entry) => entry.evidence_id);
		coverage.push({
			field_path: "price",
			status: prices.length ? "observed" : "missing",
			note: prices.length ? "仅记录页面可见价格文字；不推断券后或最终支付价。" : "未找到可核对的价格文字。",
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
			const imageCandidates = Array.from(container.querySelectorAll("img")).slice(0, Math.max(0, 40 - images.length));
			for (const image of imageCandidates) {
				if (!isVisible(image)) continue;
				if (kind === "detail") {
					if (image.closest(DETAIL_EXCLUDED_SELECTOR)) continue;
					if (image.complete && (image.naturalWidth < 240 || image.naturalHeight < 240)) continue;
				}
				const sourceUrl = imageUrlWithoutQuery(image.currentSrc || image.src, pageUrl);
				if (!sourceUrl) continue;
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
			coverage.push({
				field_path: `images.${kind}`,
				status: loadedCount ? "observed" : "missing",
				note: loadedCount ? `发现 ${loadedCount} 张已加载图片；当前只保存来源 URL 和尺寸，不保存图片字节。` : "图片区域尚无可用的已加载图片。",
				evidence_ids: imageEvidence
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
			adapter_version: "0.2.0",
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
	// src/extension/backend-collector.ts
	function collect() {
		return collectCurrentProductSnapshot(document, location.href, document.title);
	}
	exports.collect = collect;
	exports.inspectVariants = inspectVariants;
	exports.validateVariantTarget = validateVariantTarget;
	return exports;
})({});
