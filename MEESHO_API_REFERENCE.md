# Meesho Production API Reference & Engineering Notes

This document provides a technical reference of internal Meesho mobile endpoints, authentication contexts, header standards, promotional discount mechanics, and session management discovered during network analysis and system implementation.

---

## 1. Authentication & Context Segmentation

Meesho strictly partitions responses based on whether a request is **`logged_in`** or **`anonymous`**. 

| Concept | `logged_in` Context | `anonymous` Context |
| :--- | :--- | :--- |
| **`Meesho-User-Context` Header** | `"logged_in"` | `"anonymous"` |
| **`Xo` Header** | Authenticated composite token (tied to user ID) | Device/Guest composite token (`anon_xo`) |
| **`App-User-Id` Header** | Required (numeric string, e.g. `"605592597"`) | Must be omitted |
| **Primary Use Cases** | User FOD tier, order history, address, checkout | Public browsing, guest banner promotions |

---

## 2. Standard Mobile App Request Headers

Every call mimicking the Meesho Android mobile app (`com.meesho.supply`) requires the following baseline headers:

```http
Authorization: 32c4d8137cn9eb493a1921f203173080
App-Version: 29.6
App-Version-Code: 870
App-Sdk-Version: 34
App-Client-Id: android
Country-Iso: in
Application-Id: com.meesho.supply
App-Session-Count: 1
Instance-Id: <32-char hex instance UUID>
App-Gaid: <Google Advertising ID UUID>
App-Session-Id: <Session UUID>
Accept: application/json
Content-Type: application/json; charset=UTF-8
User-Agent: okhttp/4.9.0
```

### Authenticated Headers (Appended when Logged In):
```http
Xo: <composite_xo_token>
Meesho-User-Context: logged_in
App-User-Id: <user_id>
```

---

## 3. First Order Discount (FOD) Personalisation

### 3.1 Authenticated FOD Endpoint (Production User Tier)
* **Endpoint**: `POST https://prod.meeshoapi.com/api/1.0/fod-personalisation`
* **Context**: Must be `logged_in` with user's `Xo` and `App-User-Id`.
* **Payload**:
  ```json
  {
    "offer_bucket": "",
    "from_language_modal": false
  }
  ```
  > [!IMPORTANT]
  > Passing an existing bucket (e.g. `{"offer_bucket": "190"}`) will cause Meesho to echo back that bucket instead of recalculating the user's real-time eligibility. Always leave `"offer_bucket": ""`.

* **Response Structure**:
  ```json
  {
    "surgical_first_order_discount_v3": {
      "enabled": true,
      "offer_bucket": 170,
      "offer": {
        "max_offer_value": 170,
        "offer_text": "₹170 OFF",
        "offer_subtitle": "on 1st order",
        "offer_title": "Upto",
        "banner_stamp_text": "APPLIED",
        "tag_text": "Discount Applied"
      }
    }
  }
  ```
  *If the account has already placed orders or is ineligible, `enabled` is `false` or the `offer` object is absent.*

### 3.2 Anonymous FOD Endpoint (Guest Device Banners Only)
* **Endpoint**: `POST https://prod.meeshoapi.com/api/1.0/anonymous/fod-personalisation`
* **Context**: `anonymous` with guest `anon_xo`.
* **Behavior**: Returns a simulated guest-facing promotional tier (e.g. ₹190) for unauthenticated visitors. **Do not use for classifying actual user accounts**, as accounts are evaluated at user-level upon login (e.g. ₹170).

---

## 4. Headless Token Refresh (`/api/2.0/xo`)

When an account's composite `xo` token expires (`exp` in JWT passed), or returns HTTP 401:

* **Endpoint**: `POST https://prod.meeshoapi.com/api/2.0/xo`
* **Method**: POST
* **Payload**:
  ```json
  {
    "ox": "<long_lived_ox_refresh_token>"
  }
  ```
* **Response**:
  ```json
  {
    "xoox": {
      "xo": "<new_composite_xo_token>",
      "ox": "<new_ox_refresh_token>"
    }
  }
  ```
* **Composite XO Anatomy**:
  - The `xo` string consists of two parts separated by a dot: `<header_b64>.<outer_payload_b64>`.
  - Outer payload decoded: `{"jwt": "<signed_jwt>", "xo": "<internal_hex_token>"}`.
  - Inner JWT payload contains:
    - `https://meesho.com/user_id`: Numeric User ID.
    - `https://meesho.com/instance_id`: Bound instance ID.
    - `exp`: Expiration epoch timestamp.

---

## 5. Pricing, Cart & Checkout Quotes

### 5.1 Real-Time Cart Preview Quote (`/api/1.0/cart/add`)
Generates an isolated price preview without persisting in the user's permanent shopping cart session:
* **Endpoint**: `POST https://prod.meeshoapi.com/api/1.0/cart/add`
* **Payload**:
  ```json
  {
    "context": "pdp",
    "identifier": "buy_now",
    "cart_session": null,
    "replaceable": false,
    "items": [{
      "identifier": "buy_now",
      "product_id": 624039638,
      "supplier_id": 2608132,
      "variation_id": null,
      "variation": "Free Size",
      "quantity": 1,
      "selected_price_type_id": "basic_return_price",
      "client_metadata": null
    }],
    "address_id": null,
    "user_id": 605592597
  }
  ```
* **Price Breakup Analysis**:
  - `type: "PRODUCT_PRICE"`: Base item price.
  - `type: "DISCOUNT"`: Total discount deductions.
    - Sub-item `SUPPLIER_DISCOUNT`: Vendor catalog discount (e.g. ₹2).
    - Sub-item `FOD` / `SPECIAL_DISCOUNT`: Account-level first order discount.
  - `type: "ADDITIONAL_CHARGES"`: Platform or delivery fees.
  - `effective_total`: Final amount for standard payment.
  - `effective_total_for_deferred_payment` / `effective_total_for_upi_plugin`: Discounted total for online UPI payments (calculates live prepaid discount).

### 5.2 Active Cart State
* **Endpoint**: `GET https://prod.meeshoapi.com/api/9.0/cart`
* Retrieves full active cart items, subtotal, and currently applied coupons.

### 5.3 Checkout Payment & Bank Offers
* **Endpoint**: `POST https://prod.meeshoapi.com/api/v3/offers/list`
* Evaluates bank, card (Visa/Mastercard), and wallet (PhonePe, Paytm, Navi) promotions applicable to the order total.

---

## 6. Catalog, Logistics & Delivery Verification

### 6.1 Pincode Serviceability & Delivery Fees
* **Endpoint**: `GET https://prod.meeshoapi.com/api/1.0/shipping`
* **Params**:
  - `dest_pin`: 6-digit Indian pincode (e.g. `110025`)
  - `product_id`: Product ID
  - `supplier_id`: Supplier ID
  - `quantity`: 1
* **Response**:
  ```json
  {
    "shipping": {
      "serviceable": true,
      "charges": 0,
      "expected_delivery_time": "Delivery by Saturday, 25 Oct"
    }
  }
  ```

### 6.2 Product Catalog Metadata
* **Static Details**: `GET https://prod.meeshoapi.com/api/3.0/product/static?id=<product_id>&context=search&ad_active=true`
  - Returns product title, images, specifications, size/variation IDs, and in-stock flags.
* **Dynamic Details**: `GET https://prod.meeshoapi.com/api/3.0/product/dynamic?id=<product_id>&context=search&origin=catalog_search_results`
  - Returns real-time supplier list, base prices, and vendor-level prepaid discount percentages (`prepaid_total_discount_in_rs`).

---

## 7. Account Order History & Verification

* **Endpoint**: `POST https://prod.meeshoapi.com/api/3.0/user/orders`
* **Payload**:
  ```json
  {
    "limit": 20,
    "filters": {},
    "user_id": 605592597
  }
  ```
* **Fresh vs. Used Determination**:
  - If `orders` list is empty (`len == 0`), the account is classified as **Fresh** (eligible for First Order Discounts).
  - If `orders` list has entries (`len > 0`), the account is classified as **Used** (ineligible for FOD).

