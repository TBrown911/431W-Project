import hashlib
import sqlite3
import secrets
from datetime import datetime
from flask import Flask, g, render_template, request, redirect, url_for, session, jsonify


app = Flask(__name__)
secret_key = secrets.token_hex(16)
app.config['SECRET_KEY'] = secret_key


def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def get_db():
    if not hasattr(g, 'sqlite_db'):
        g.sqlite_db = sqlite3.connect('auction-house.db')
    return g.sqlite_db


@app.teardown_appcontext
def close_db(error):
    if hasattr(g, 'sqlite_db'):
        g.sqlite_db.close()


@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        session['email'] = email
        password = request.form['password']
        db = get_db()
        c = db.cursor()
        c.execute("SELECT * FROM Users WHERE email=?", (email,))
        user = c.fetchone()

        if user is None:
            error = "Email not found."
            return render_template('login.html', error=error)
        else:
            hashed_password = hash_password(password)
            if hashed_password == user[1]:
                return redirect(url_for('welcome'))
            else:
                error = "Incorrect password."
                return render_template('login.html', error=error)
    else:
        return render_template('login.html')


@app.route('/myaccount')
def myaccount():
    db = get_db()
    c = db.cursor()
    email = session['email']

    # Retrieve user information from the database
    c.execute("SELECT b.first_name, b.last_name, b.email, b.age, b.gender, b.major, "
              "a.street_num || ' ' || a.street_name AS street, z.city, z.state, "
              "a.zipcode, substr(c.credit_card_num, -4) AS credit_card_last_4 "
              "FROM Bidders b "
              "LEFT JOIN Address a ON b.home_address_id = a.address_ID "
              "LEFT JOIN Zipcode_Info z ON a.zipcode = z.zipcode "
              "LEFT JOIN Credit_Cards c ON b.email = c.Owner_email "
              "WHERE b.email=?", (email,))
    user = c.fetchone()
    # Pass the user information to the template
    return render_template('myaccount.html', first_name=user[0], last_name=user[1], email=user[2],
                           age=user[3], gender=user[4], major=user[5], street=user[6], city=user[7],
                           state=user[8], zipcode=user[9], credit_card_last_4=user[10])


@app.route('/welcome')
def welcome():
    db = get_db()
    c = db.cursor()
    email = session['email']

    # Join the Users and Bidders tables on the email column
    c.execute("SELECT b.first_name, b.last_name "
              "FROM Bidders b "
              "WHERE b.email=?", (email,))
    user = c.fetchone()
    return render_template('welcome.html', email=email, first_name=user[0], last_name=user[1])


@app.route('/search', methods=['GET', 'POST'])
def search():
    db = get_db()
    c = db.cursor()

    # Fetch parent categories and subcategories from Categories table
    c.execute("SELECT DISTINCT parent_category FROM Categories ORDER BY parent_category")
    parent_categories = [row[0] for row in c.fetchall()]

    c.execute("SELECT parent_category, category_name FROM Categories ORDER BY parent_category, category_name")
    subcategories_by_parent_category = {}
    for row in c.fetchall():
        parent_category = row[0]
        subcategory = row[1]
        if parent_category not in subcategories_by_parent_category:
            subcategories_by_parent_category[parent_category] = []
        subcategories_by_parent_category[parent_category].append(subcategory)

    # Fetch data from Auction_Listings table
    # Get search parameters from request.form
    category = request.form.get('category', '')
    subcategory = request.form.get('subcategory', '')
    status = request.form.get('status', '')
    product_name = request.form.get('product_name', '')

    # Construct SQL query with search parameters
    query = "SELECT Auction_Listings.* FROM Auction_Listings"
    query += " INNER JOIN Categories ON Auction_Listings.category = Categories.category_name"
    if category:
        query += f" AND Categories.parent_category = '{category}'"
    if subcategory:
        query += f" AND Categories.category_name = '{subcategory}'"
    if status:
        query += f" AND Auction_Listings.status = '{status}'"
    if product_name:
        query += f" AND Auction_Listings.product_name LIKE '%{product_name}%'"
    query += " ORDER BY listing_id"

    c.execute(query)
    auction_listings = c.fetchall()

    # Pass the results to the template
    return render_template('categories.html', parent_categories=parent_categories,
                           subcategories_by_parent_category=subcategories_by_parent_category,
                           auction_listings=auction_listings)


@app.route('/auction_detail/<listing_id>')
def auction_detail(listing_id):
    db = get_db()
    c = db.cursor()

    auction_listing = get_auction_listing(listing_id)

    # Render the auction_detail template with the auction details
    return render_template('auctiondetail.html', auction_listing=auction_listing)


def get_auction_listing(listing_id):
    db = get_db()
    c = db.cursor()

    # Retrieve the auction details based on the listing ID
    c.execute("SELECT Auction_Listings.*, "
              "ROUND(AVG(Rating.rating), 1) AS avg_rating, "
              "'$' || CAST(COALESCE(b.max_bid, 0) AS TEXT) AS max_bid, "
              "(Auction_Listings.max_bids - COALESCE(b.num_bids, 0)) AS remaining_bids "
              "FROM Auction_Listings "
              "LEFT JOIN Rating ON Auction_Listings.seller_Email = Rating.seller_email "
              "LEFT JOIN (SELECT Listing_ID, CAST(MAX(Bid_price) AS INTEGER) AS max_bid, COUNT(*) AS num_bids "
              "           FROM Bids "
              "           WHERE Listing_ID = ? "
              "           GROUP BY Listing_ID) "
              "b ON Auction_Listings.Listing_ID = b.Listing_ID "
              "WHERE Auction_Listings.Listing_ID = ?", (listing_id, listing_id))
    auction_listing = c.fetchone()

    return auction_listing


@app.route('/place_bid', methods=['POST'])
def place_bid():
    # get input data from POST request
    bidder_email = request.form['bidder_email']
    seller_email = request.form['seller_email']
    listing_id = request.form['listing_id']
    bid_price = int(request.form['bid_amount'])
    remaining_bids = int(request.form['remaining_bids']) if request.form['remaining_bids'] else 0

    db = get_db()
    c = db.cursor()

    # retrieve reserve price from Auction_Listings table
    c.execute("SELECT Reserve_price FROM Auction_Listings WHERE Seller_Email=? AND Listing_ID=?",
              (seller_email, listing_id))
    reserve_price_row = c.fetchone()
    reserve_price = float(reserve_price_row[0][1:])  # remove '$' from string and convert to float

    # check if bid is valid
    if remaining_bids == 0:
        return jsonify({'success': False, 'message': 'Auction has ended or reached maximum number of bids'})

    if reserve_price is not None and bid_price <= reserve_price:
        return jsonify({'success': False, 'message': 'Bid price must be higher than the reserve price'})

    c.execute("SELECT Bid_price "
              "FROM Bids "
              "WHERE Seller_Email=? AND Listing_ID=? "
              "ORDER BY Bid_price DESC "
              "LIMIT 1", (seller_email, listing_id))
    highest_bid_row = c.fetchone()
    highest_bid = int(highest_bid_row[0]) if highest_bid_row is not None else None
    if highest_bid is not None and bid_price <= highest_bid:
        return jsonify({'success': False, 'message': 'Bid price must be higher than previous bids'})

    # insert bid into Bids table
    c.execute("INSERT INTO Bids (Seller_Email, Listing_ID, Bidder_email, Bid_price) VALUES (?, ?, ?, ?)", (seller_email, listing_id, bidder_email, bid_price))
    bid_id = c.lastrowid
    db.commit()

    # get updated auction details
    c.execute("SELECT * FROM Auction_Listings "
              "WHERE Seller_Email=? AND Listing_ID=?", (seller_email, listing_id))

    # update remaining bids and return updated auction details
    remaining_bids -= 1
    if remaining_bids == 0:
        # set status of bid to 'sold' in Auction_Listings table
        c.execute("UPDATE Auction_Listings SET Status = 2 WHERE Seller_Email = ? AND Listing_ID = ?",
                  (seller_email, listing_id))
        db.commit()
        c.execute("SELECT Auction_Listings.product_name, Auction_Listings.listing_id, Bids.Bid_price, Auction_Listings.seller_email "
                  "FROM Auction_Listings "
                  "JOIN Bids ON Auction_Listings.Seller_Email = Bids.Seller_Email AND Auction_Listings.Listing_ID = Bids.Listing_ID "
                  "WHERE Auction_Listings.Seller_Email = ? AND Auction_Listings.Listing_ID = ?",
                  (seller_email, listing_id))
        auction_listings = c.fetchone()
        auction_listings_with_bid_id = auction_listings + (bid_id,)
        return render_template('purchase.html', auction_listings=auction_listings_with_bid_id)

    auction_listing = get_auction_listing(listing_id)

    # Render the auction_detail template with the auction details
    return render_template('auctiondetail.html', auction_listing=auction_listing)


@app.route('/purchase', methods=['POST'])
def purchase():
    # retrieve credit card information from the form
    credit_card_num = request.form['card-number']
    formatted_cc_num = '-'.join([credit_card_num[i:i + 4] for i in range(0, len(credit_card_num), 4)])
    card_type = request.form['card-type']
    expiry_date = request.form['expiry-date']
    expiry_month, expiry_year = expiry_date.split('/')
    security_code = request.form['cvv']
    buyer_email = request.form['bidder_email']
    bid_id = request.form['bid-id']
    listing_id = request.form['listing-id']
    seller_email = request.form['seller-email']
    current_date = datetime.now().date()
    formatted_date = current_date.strftime('%-m/%-d/%y')

    db = get_db()
    c = db.cursor()

    c.execute("SELECT bid_price FROM Bids WHERE bid_id = ?", (bid_id,))
    amount = int(c.fetchone()[0])

    c.execute("INSERT OR IGNORE INTO Credit_Cards (credit_card_num, card_type, expire_month, expire_year, security_code, owner_email) "
              "VALUES (?, ?, ?, ?, ?, ?)",
              (formatted_cc_num, card_type, expiry_month, expiry_year, security_code, buyer_email))
    db.commit()

    c.execute("INSERT INTO Transactions (seller_email, listing_id, buyer_email, date, payment)"
              "VALUES (?, ?, ?, ?, ?)",
              (seller_email, listing_id, buyer_email, formatted_date, amount))
    db.commit()

    c.execute(
        "SELECT Auction_Listings.seller_email, Auction_Listings.category, Auction_Listings.product_name, MAX(CAST(Bids.bid_price as INTEGER)) "
        "FROM Bids "
        "JOIN Auction_Listings ON Bids.Listing_ID = Auction_Listings.Listing_ID "
        "WHERE Bids.Bidder_email = ? AND Auction_Listings.Status = 1 "
        "GROUP BY Auction_Listings.listing_id", (buyer_email,))

    active_bids = c.fetchall()

    return render_template('mybids.html', active_bids=active_bids)


@app.route('/mybids')
def my_bids():
    email = session['email']

    db = get_db()
    c = db.cursor()

    c.execute(
        "SELECT Auction_Listings.seller_email, Auction_Listings.category, Auction_Listings.product_name, MAX(CAST(Bids.bid_price as INTEGER)) "
        "FROM Bids "
        "JOIN Auction_Listings ON Bids.Listing_ID = Auction_Listings.Listing_ID "
        "WHERE Bids.Bidder_email = ? AND Auction_Listings.Status = 1 "
        "GROUP BY Auction_Listings.listing_id", (email,))

    active_bids = c.fetchall()

    return render_template('mybids.html', active_bids=active_bids)


@app.route('/logout')
def logout():
    # Clear the session data
    session.clear()
    # Redirect to the login page
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(debug=True)
